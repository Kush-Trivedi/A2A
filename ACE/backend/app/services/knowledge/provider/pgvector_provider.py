import json
import uuid
from sqlalchemy import bindparam, text
from ....utils.common.logger import Logger
from ....utils.errors import DatabaseError, ValidationError
from ....entity.document.document_entity import DocumentEntity
from ..settings import KnowledgeSettings, get_knowledge_settings
from ....database.rdbms.pg_session import get_postgres_connector
from ....entity.pgvector.document_chunk_entity import DocumentChunkEntity
from .base import IngestDocument, KnowledgeChunk, KnowledgeProvider, RetrievalQuery

logger = Logger(__name__).get_logger()

_STATUS_PROCESSED = "processed"
_STATUS_ARCHIVED = "archived"
_RETRIEVAL_MODES = {"dense", "sparse", "hybrid"}


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in embedding) + "]"


class PgVectorKnowledgeProvider(KnowledgeProvider):
    def __init__(self, settings: KnowledgeSettings | None = None) -> None:
        self._connector = get_postgres_connector()
        self._settings = settings or get_knowledge_settings()

    async def upsert(self, document: IngestDocument) -> str:
        document_id = uuid.uuid4().hex
        doc_metadata = {
            **document.metadata,
            "knowledge_source": document.knowledge_source,
        }
        if document.session_id:
            doc_metadata["session_id"] = document.session_id

        doc_entity = DocumentEntity(
            id=document_id,
            tenant_id=document.tenant_id,
            actor_id=document.actor_id,
            source_type=document.source_type,
            source_name=document.source_name,
            status=_STATUS_PROCESSED,
            sha256=document.sha256,
            size_bytes=document.size_bytes,
            chunk_count=len(document.chunks),
            metadata_json=doc_metadata,
        )

        chunk_entities = [
            DocumentChunkEntity(
                id=uuid.uuid4().hex,
                tenant_id=document.tenant_id,
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding_text=chunk.embedding_text or chunk.content,
                token_count_estimate=chunk.token_count,
                embedding=chunk.embedding,
                embedding_model=document.embedding_model,
                embedding_dimensions=document.embedding_dimensions,
                metadata_json={
                    **chunk.metadata,
                    "knowledge_source": document.knowledge_source,
                    **({"session_id": document.session_id} if document.session_id else {}),
                },
            )
            for chunk in document.chunks
        ]

        try:
            async with self._connector.session() as session:
                session.add(doc_entity)
                for entity in chunk_entities:
                    session.add(entity)
        except Exception as exc:  # noqa: BLE001
            logger.error("pgvector upsert failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

        logger.info(
            "Document ingested",
            extra={
                "document_id": document_id,
                "knowledge_source": document.knowledge_source,
                "chunks": len(chunk_entities),
            },
        )
        return document_id

    async def find_document_by_hash(
        self,
        *,
        tenant_id: str,
        sha256: str,
        knowledge_source: str,
        session_id: str | None = None,
    ) -> tuple[str, int] | None:
        params: dict[str, object] = {
            "tenant_id": tenant_id,
            "sha256": sha256,
            "knowledge_source": knowledge_source,
        }
        if session_id:
            scope = "AND metadata->>'session_id' = :session_id"
            params["session_id"] = session_id
        else:
            scope = "AND (metadata ? 'session_id') IS NOT TRUE"

        sql = text(
            f"""
            SELECT id, chunk_count
            FROM documents
            WHERE tenant_id = :tenant_id
              AND sha256 = :sha256
              AND status = '{_STATUS_PROCESSED}'
              AND metadata->>'knowledge_source' = :knowledge_source
              {scope}
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        try:
            async with self._connector.session() as session:
                row = (await session.execute(sql, params)).mappings().first()
        except Exception as exc:
            logger.error(
                "pgvector dedup lookup failed", extra={"error": str(exc)}, exc_info=True
            )
            raise DatabaseError(cause=exc) from exc

        if row is None:
            return None
        return str(row["id"]), int(row["chunk_count"] or 0)

    async def retrieve(self, query: RetrievalQuery) -> list[KnowledgeChunk]:
        if not query.knowledge_sources and not query.session_id:
            return []

        top_k = max(1, query.top_k)
        candidates = max(top_k, query.candidate_pool or self._settings.retrieval_candidates)
        base_window = (
            query.neighbor_window
            if query.neighbor_window > 0
            else self._settings.neighbor_window
        )
        max_window = max(
            base_window,
            query.neighbor_max_window or self._settings.neighbor_max_window,
        )
        score_floor = (
            query.neighbor_score_floor
            if query.neighbor_score_floor > 0
            else self._settings.neighbor_score_floor
        )
        halo = 1 + 2 * max(1, base_window)
        token_budget = (
            query.context_token_budget
            or self._settings.context_token_budget
            or top_k * self._settings.chunk_size * halo
        )
        mode = (query.mode or self._settings.retrieval_mode).strip().lower()
        if mode not in _RETRIEVAL_MODES:
            raise ValidationError(
                f"Unknown retrieval mode '{mode}'. "
                f"Valid modes: {', '.join(sorted(_RETRIEVAL_MODES))}."
            )
        if mode in {"sparse", "hybrid"} and not query.query_text.strip():
            logger.warning(
                "Retrieval mode '%s' requires query text; falling back to dense.", mode
            )
            mode = "dense"

        scope_clauses: list[str] = []
        params: dict[str, object] = {
            "tenant_id": query.tenant_id,
            "qvec": _vector_literal(query.embedding),
            "candidates": candidates,
            "top_k": top_k,
            "rrf_k": self._settings.rrf_k,
            "query_text": query.query_text,
        }
        if query.knowledge_sources:
            scope_clauses.append("(c.metadata->>'knowledge_source') IN :sources")
            params["sources"] = list(query.knowledge_sources)
        if query.session_id:
            scope_clauses.append("(c.metadata->>'session_id') = :session_id")
            params["session_id"] = query.session_id
        scope_sql = " OR ".join(scope_clauses)

        try:
            async with self._connector.session() as session:
                if mode == "hybrid":
                    seeds = await self._fetch_hybrid_seeds(
                        session, scope_sql, params, query.knowledge_sources
                    )
                elif mode == "sparse":
                    seeds = await self._fetch_sparse_seeds(
                        session, scope_sql, params, query.knowledge_sources
                    )
                else:
                    seeds = await self._fetch_vector_seeds(
                        session, scope_sql, params, query
                    )
                rows = await self._expand_neighbors(
                    session,
                    query.tenant_id,
                    seeds,
                    base_window=base_window,
                    max_window=max_window,
                    score_floor=score_floor,
                    token_budget=token_budget,
                    top_k=top_k,
                )
        except DatabaseError:
            raise
        except Exception as exc:
            logger.error("pgvector retrieval failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

        hits: list[KnowledgeChunk] = []
        for row in rows:
            metadata = dict(row["metadata"] or {})
            hits.append(
                KnowledgeChunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    source_name=row["source_name"] or "",
                    knowledge_source=str(metadata.get("knowledge_source", "")),
                    content=row["content"],
                    score=float(row["score"] or 0.0),
                    metadata=metadata,
                )
            )
        return hits

    async def _fetch_vector_seeds(
        self, session, scope_sql: str, params: dict, query: RetrievalQuery
    ) -> list[dict]:
        sql = text(
            f"""
            SELECT c.id AS chunk_id,
                   c.document_id AS document_id,
                   c.chunk_index AS chunk_index,
                   c.content AS content,
                   c.metadata AS metadata,
                   c.token_count_estimate AS token_count,
                   d.source_name AS source_name,
                   1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.tenant_id = :tenant_id
              AND c.embedding IS NOT NULL
              AND d.status = '{_STATUS_PROCESSED}'
              AND ({scope_sql})
            ORDER BY c.embedding <=> CAST(:qvec AS vector)
            LIMIT :top_k
            """
        )
        if query.knowledge_sources:
            sql = sql.bindparams(bindparam("sources", expanding=True))
        result = await session.execute(sql, params)
        rows = result.mappings().all()
        return [
            dict(r) for r in rows if float(r["score"] or 0.0) >= query.min_similarity
        ]

    async def _fetch_sparse_seeds(
        self, session, scope_sql: str, params: dict, sources: tuple[str, ...]
    ) -> list[dict]:
        sql = text(
            f"""
            SELECT c.id AS chunk_id,
                   c.document_id AS document_id,
                   c.chunk_index AS chunk_index,
                   c.content AS content,
                   c.metadata AS metadata,
                   c.token_count_estimate AS token_count,
                   d.source_name AS source_name,
                   ts_rank_cd(c.search_vector, q.tsq) AS score
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            CROSS JOIN (
                SELECT regexp_replace(plainto_tsquery('english', :query_text)::text, '&', '|', 'g')::tsquery AS tsq
            ) q
            WHERE c.tenant_id = :tenant_id
              AND d.status = '{_STATUS_PROCESSED}'
              AND c.search_vector @@ q.tsq
              AND ({scope_sql})
            ORDER BY ts_rank_cd(c.search_vector, q.tsq) DESC
            LIMIT :top_k
            """
        )
        if sources:
            sql = sql.bindparams(bindparam("sources", expanding=True))
        result = await session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    async def _fetch_hybrid_seeds(
        self, session, scope_sql: str, params: dict, sources: tuple[str, ...]
    ) -> list[dict]:
        sql = text(
            f"""
            WITH semantic AS (
                SELECT c.id AS chunk_id, c.document_id, c.chunk_index,
                       c.content, c.metadata, c.token_count_estimate AS token_count,
                       d.source_name,
                       row_number() OVER (
                           ORDER BY c.embedding <=> CAST(:qvec AS vector)
                       ) AS rnk
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.tenant_id = :tenant_id
                  AND c.embedding IS NOT NULL
                  AND d.status = '{_STATUS_PROCESSED}'
                  AND ({scope_sql})
                ORDER BY c.embedding <=> CAST(:qvec AS vector)
                LIMIT :candidates
            ),
            lexical AS (
                SELECT c.id AS chunk_id, c.document_id, c.chunk_index,
                       c.content, c.metadata, c.token_count_estimate AS token_count,
                       d.source_name,
                       row_number() OVER (
                           ORDER BY ts_rank_cd(c.search_vector, q.tsq) DESC
                       ) AS rnk
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                CROSS JOIN (
                    SELECT regexp_replace(plainto_tsquery('english', :query_text)::text, '&', '|', 'g')::tsquery AS tsq
                ) q
                WHERE c.tenant_id = :tenant_id
                  AND d.status = '{_STATUS_PROCESSED}'
                  AND c.search_vector @@ q.tsq
                  AND ({scope_sql})
                ORDER BY ts_rank_cd(c.search_vector, q.tsq) DESC
                LIMIT :candidates
            ),
            fused AS (
                SELECT chunk_id, document_id, chunk_index, content, metadata,
                       token_count, source_name, 1.0 / (:rrf_k + rnk) AS score
                FROM semantic
                UNION ALL
                SELECT chunk_id, document_id, chunk_index, content, metadata,
                       token_count, source_name, 1.0 / (:rrf_k + rnk) AS score
                FROM lexical
            )
            SELECT chunk_id, document_id, chunk_index, content, metadata,
                   token_count, source_name, SUM(score) AS score
            FROM fused
            GROUP BY chunk_id, document_id, chunk_index, content, metadata,
                     token_count, source_name
            ORDER BY score DESC, chunk_index ASC
            LIMIT :top_k
            """
        )
        if sources:
            sql = sql.bindparams(bindparam("sources", expanding=True))
        result = await session.execute(sql, params)
        return [dict(r) for r in result.mappings().all()]

    async def _expand_neighbors(
        self,
        session,
        tenant_id: str,
        seeds: list[dict],
        *,
        base_window: int,
        max_window: int,
        score_floor: float,
        token_budget: int,
        top_k: int,
    ) -> list[dict]:
        merged: dict[str, dict] = {r["chunk_id"]: dict(r) for r in seeds}
        if not seeds or max_window <= 0:
            return self._budgeted(merged, seeds, token_budget, top_k)

        top_score = max((float(r["score"] or 0.0) for r in seeds), default=0.0) or 1.0
        windows: list[dict] = []
        for seed in seeds:
            rel = (float(seed["score"] or 0.0)) / top_score
            if rel >= 0.75:
                width = max_window
            elif rel >= score_floor:
                width = base_window
            else:
                continue
            if width <= 0:
                continue
            idx = int(seed["chunk_index"])
            windows.append(
                {
                    "document_id": seed["document_id"],
                    "start_index": idx - width,
                    "end_index": idx + width,
                    "seed_score": float(seed["score"] or 0.0),
                }
            )

        if not windows:
            return self._budgeted(merged, seeds, token_budget, top_k)

        sql = text(
            f"""
            WITH w AS (
                SELECT * FROM jsonb_to_recordset(CAST(:windows AS jsonb))
                AS w(document_id text, start_index int, end_index int,
                     seed_score double precision)
            )
            SELECT c.id AS chunk_id, c.document_id, c.chunk_index,
                   c.content, c.metadata, c.token_count_estimate AS token_count,
                   d.source_name,
                   MAX(w.seed_score) * :decay AS score
            FROM w
            JOIN document_chunks c ON c.document_id = w.document_id
             AND c.chunk_index BETWEEN w.start_index AND w.end_index
            JOIN documents d ON d.id = c.document_id
            WHERE c.tenant_id = :tenant_id
              AND d.status = '{_STATUS_PROCESSED}'
            GROUP BY c.id, c.document_id, c.chunk_index, c.content, c.metadata,
                     c.token_count_estimate, d.source_name
            """
        )
        result = await session.execute(
            sql,
            {
                "windows": json.dumps(windows),
                "decay": self._settings.neighbor_decay,
                "tenant_id": tenant_id,
            },
        )
        for row in result.mappings().all():
            existing = merged.get(row["chunk_id"])
            if existing is None:
                merged[row["chunk_id"]] = dict(row)
            elif float(row["score"] or 0.0) > float(existing["score"] or 0.0):
                existing["score"] = row["score"]

        return self._budgeted(merged, seeds, token_budget, top_k)

    @staticmethod
    def _budgeted(
        merged: dict[str, dict], seeds: list[dict], token_budget: int, top_k: int
    ) -> list[dict]:
        seed_ids = {r["chunk_id"] for r in seeds}
        ordered = sorted(
            merged.values(),
            key=lambda r: (float(r["score"] or 0.0), -int(r["chunk_index"])),
            reverse=True,
        )

        def _tokens(row: dict) -> int:
            tc = row.get("token_count")
            return int(tc) if tc else 0

        if not any(_tokens(r) for r in ordered):
            return ordered[: max(top_k, len(seed_ids)) * 3]

        selected: list[dict] = []
        used = 0
        for row in ordered:
            if row["chunk_id"] in seed_ids:
                selected.append(row)
                used += _tokens(row)
        for row in ordered:
            if row["chunk_id"] in seed_ids:
                continue
            cost = _tokens(row)
            if used + cost > token_budget and selected:
                continue
            selected.append(row)
            used += cost
        selected.sort(
            key=lambda r: (float(r["score"] or 0.0), -int(r["chunk_index"])),
            reverse=True,
        )
        return selected


    async def soft_delete_session_uploads(
        self, *, tenant_id: str, session_id: str
    ) -> int:
        sql = text(
            f"""
            UPDATE documents
            SET status = '{_STATUS_ARCHIVED}'
            WHERE tenant_id = :tenant_id
              AND status = '{_STATUS_PROCESSED}'
              AND metadata->>'session_id' = :session_id
            """
        )
        try:
            async with self._connector.session() as session:
                result = await session.execute(
                    sql, {"tenant_id": tenant_id, "session_id": session_id}
                )
                return int(result.rowcount or 0)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "pgvector session upload soft-delete failed",
                extra={"error": str(exc)},
                exc_info=True,
            )
            raise DatabaseError(cause=exc) from exc
