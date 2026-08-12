"""Grant-scoped retrieval over the knowledge store — the capability every
agent calls instead of owning a vector store.

Access model: an agent sees ONLY chunks of documents granted to it in
document_grants. The grant join is inside every SQL path, so isolation is
structural, not a filter someone can forget.

Scoring (all numbers from yaml knowledge.retrieval):
  dense   — HNSW cosine over the chunk embeddings
  sparse  — GIN full-text over the generated tsvector (AND relaxed to OR)
  hybrid  — Reciprocal Rank Fusion of both rank lists: sum 1/(rrf_k + rank)
  graph   — 1-hop GraphRAG expansion: the query matches entity nodes by
            embedding, walks one hop of edges, and pulls in chunks that
            MENTION those entities (score decays per hop)
  finally — top seeds pull their +-window neighbours (decayed) so answers
            keep local context

Everything is async; the queries ride the HNSW/GIN indexes built at startup.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlalchemy import text as sql_text

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, ValidationError
from .embedding_service import EmbeddingService, get_embedding_service

logger = Logger(__name__).get_logger()

RETRIEVAL_MODES = ("dense", "sparse", "hybrid")

_PROVENANCE_COLUMNS = """
       c.id AS chunk_id, c.document_id AS document_id, c.chunk_index AS chunk_index,
       c.content AS content, c.token_count AS token_count, c.metadata AS metadata,
       d.file_name AS file_name, d.source_uri AS source_uri
"""

_GRANT_JOIN = """
JOIN document_grants g ON g.document_id = c.document_id
 AND g.tenant_id = :tenant_id AND g.agent_key = :agent_key
JOIN documents d ON d.id = c.document_id
"""


@dataclass(frozen=True)
class RetrievalSettings:
    mode: str
    top_k: int
    candidates: int
    min_similarity: float
    rrf_k: int
    neighbor_window: int
    neighbor_decay: float
    graph_enabled: bool
    graph_entity_top_k: int
    graph_entity_min_similarity: float
    graph_hop_decay: float


@lru_cache(maxsize=1)
def get_retrieval_settings() -> RetrievalSettings:
    retrieval = get_application_context().knowledge["retrieval"]
    graph = retrieval["graph"]
    settings = RetrievalSettings(
        mode=str(retrieval["mode"]).strip().lower(),
        top_k=int(retrieval["top_k"]),
        candidates=int(retrieval["candidates"]),
        min_similarity=float(retrieval["min_similarity"]),
        rrf_k=int(retrieval["rrf_k"]),
        neighbor_window=int(retrieval["neighbor_window"]),
        neighbor_decay=float(retrieval["neighbor_decay"]),
        graph_enabled=bool(graph["enabled"]),
        graph_entity_top_k=int(graph["entity_top_k"]),
        graph_entity_min_similarity=float(graph["entity_min_similarity"]),
        graph_hop_decay=float(graph["hop_decay"]),
    )
    if settings.mode not in RETRIEVAL_MODES:
        raise ValidationError(
            f"knowledge.retrieval.mode '{settings.mode}' is unknown. "
            f"Valid modes: {', '.join(RETRIEVAL_MODES)}."
        )
    return settings


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    file_name: str
    source_uri: str
    content: str
    token_count: int
    score: float
    origin: str  # dense | sparse | hybrid | graph | neighbor
    metadata: dict[str, Any] = field(default_factory=dict)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


class RetrievalService:
    def __init__(self, embeddings: EmbeddingService | None = None) -> None:
        # Lazy: sparse-only retrieval must work with no embedding endpoint
        # configured; the service is built the first time vectors are needed.
        self._embeddings_override = embeddings
        self._db = get_postgres_connector()
        self._settings = get_retrieval_settings()

    @property
    def _embeddings(self) -> EmbeddingService:
        if self._embeddings_override is None:
            self._embeddings_override = get_embedding_service()
        return self._embeddings_override

    async def retrieve(
        self,
        *,
        tenant_id: str,
        agent_key: str,
        query: str,
        mode: str | None = None,
        top_k: int | None = None,
        min_similarity: float | None = None,
    ) -> list[RetrievedChunk]:
        question = (query or "").strip()
        if not question:
            raise ValidationError("Retrieval needs a non-empty query.")
        resolved_mode = (mode or self._settings.mode).strip().lower()
        if resolved_mode not in RETRIEVAL_MODES:
            raise ValidationError(
                f"Unknown retrieval mode '{resolved_mode}'. "
                f"Valid modes: {', '.join(RETRIEVAL_MODES)}."
            )
        limit = max(1, min(top_k or self._settings.top_k, 50))
        floor = (
            self._settings.min_similarity
            if min_similarity is None
            else max(0.0, min(min_similarity, 1.0))
        )

        query_vector: list[float] | None = None
        if resolved_mode in ("dense", "hybrid") or self._settings.graph_enabled:
            query_vector = (await self._embeddings.embed([question]))[0]

        params = {"tenant_id": tenant_id, "agent_key": agent_key.strip().lower()}
        try:
            async with self._db.session() as session:
                if resolved_mode == "dense":
                    seeds = await self._dense_seeds(session, params, query_vector, floor)
                elif resolved_mode == "sparse":
                    seeds = await self._sparse_seeds(session, params, question)
                else:
                    dense = await self._dense_seeds(session, params, query_vector, floor)
                    sparse = await self._sparse_seeds(session, params, question)
                    seeds = self._fuse(dense, sparse)

                merged: dict[str, dict] = {s["chunk_id"]: s for s in seeds}
                if self._settings.graph_enabled and query_vector is not None:
                    for row in await self._graph_chunks(session, params, query_vector):
                        self._keep_best(merged, row)

                top = sorted(
                    merged.values(), key=lambda r: float(r["score"]), reverse=True
                )[:limit]
                if self._settings.neighbor_window > 0 and top:
                    for row in await self._neighbors(session, params, top):
                        self._keep_best(merged, row)
        except (DatabaseError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        ordered = sorted(merged.values(), key=lambda r: float(r["score"]), reverse=True)
        # top_k seeds + their window neighbours is the natural result size
        cap = limit * (1 + 2 * self._settings.neighbor_window)
        hits = [self._to_chunk(row) for row in ordered[:cap]]
        logger.info(
            "Retrieval finished",
            extra={
                "agent_key": params["agent_key"],
                "mode": resolved_mode,
                "hits": len(hits),
            },
        )
        return hits

    # --- seed queries -------------------------------------------------------

    async def _dense_seeds(
        self, session, params: dict, query_vector: list[float], floor: float
    ) -> list[dict]:
        statement = sql_text(
            f"""
            SELECT {_PROVENANCE_COLUMNS},
                   1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
            FROM document_chunks c
            {_GRANT_JOIN}
            WHERE c.tenant_id = :tenant_id AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:qvec AS vector)
            LIMIT :candidates
            """
        )
        rows = (
            await session.execute(
                statement,
                {
                    **params,
                    "qvec": _vector_literal(query_vector),
                    "candidates": self._settings.candidates,
                },
            )
        ).mappings().all()
        return [
            {**dict(row), "origin": "dense"}
            for row in rows
            if float(row["score"] or 0.0) >= floor
        ]

    async def _sparse_seeds(self, session, params: dict, question: str) -> list[dict]:
        statement = sql_text(
            f"""
            WITH q AS (
                SELECT regexp_replace(
                    plainto_tsquery('english', :question)::text, '&', '|', 'g'
                )::tsquery AS tsq
            )
            SELECT {_PROVENANCE_COLUMNS},
                   ts_rank_cd(c.search_vector, q.tsq) AS score
            FROM document_chunks c
            {_GRANT_JOIN}
            CROSS JOIN q
            WHERE c.tenant_id = :tenant_id AND c.search_vector @@ q.tsq
            ORDER BY ts_rank_cd(c.search_vector, q.tsq) DESC
            LIMIT :candidates
            """
        )
        rows = (
            await session.execute(
                statement,
                {**params, "question": question, "candidates": self._settings.candidates},
            )
        ).mappings().all()
        return [{**dict(row), "origin": "sparse"} for row in rows]

    def _fuse(self, dense: list[dict], sparse: list[dict]) -> list[dict]:
        """Reciprocal Rank Fusion — rank-based, so the incomparable cosine
        and ts_rank scales never mix directly."""
        fused: dict[str, dict] = {}
        for ranked in (dense, sparse):
            for rank, row in enumerate(ranked, start=1):
                score = 1.0 / (self._settings.rrf_k + rank)
                existing = fused.get(row["chunk_id"])
                if existing is None:
                    fused[row["chunk_id"]] = {**row, "score": score, "origin": "hybrid"}
                else:
                    existing["score"] = float(existing["score"]) + score
        return sorted(fused.values(), key=lambda r: float(r["score"]), reverse=True)

    # --- 1-hop graph expansion ---------------------------------------------

    async def _graph_chunks(
        self, session, params: dict, query_vector: list[float]
    ) -> list[dict]:
        nodes = (
            await session.execute(
                sql_text(
                    """
                    SELECT n.id AS node_id,
                           1 - (n.embedding <=> CAST(:qvec AS vector)) AS score
                    FROM knowledge_graph_nodes n
                    WHERE n.tenant_id = :tenant_id AND n.embedding IS NOT NULL
                    ORDER BY n.embedding <=> CAST(:qvec AS vector)
                    LIMIT :entity_top_k
                    """
                ),
                {
                    "tenant_id": params["tenant_id"],
                    "qvec": _vector_literal(query_vector),
                    "entity_top_k": self._settings.graph_entity_top_k,
                },
            )
        ).mappings().all()
        matched = [
            {"node_id": row["node_id"], "score": float(row["score"])}
            for row in nodes
            if float(row["score"] or 0.0) >= self._settings.graph_entity_min_similarity
        ]
        if not matched:
            return []

        statement = sql_text(
            f"""
            WITH matched AS (
                SELECT * FROM jsonb_to_recordset(CAST(:matched AS jsonb))
                AS m(node_id text, score double precision)
            ),
            hop AS (
                SELECT m.node_id, m.score, 0 AS distance FROM matched m
                UNION
                SELECT CASE WHEN e.src_node_id = m.node_id
                            THEN e.dst_node_id ELSE e.src_node_id END,
                       m.score, 1
                FROM knowledge_graph_edges e
                JOIN matched m ON m.node_id IN (e.src_node_id, e.dst_node_id)
                WHERE e.tenant_id = :tenant_id
            )
            SELECT {_PROVENANCE_COLUMNS},
                   MAX(h.score * POWER(CAST(:hop_decay AS double precision), h.distance)) AS score
            FROM hop h
            JOIN chunk_entity_mentions cem
              ON cem.node_id = h.node_id AND cem.tenant_id = :tenant_id
            JOIN document_chunks c ON c.id = cem.chunk_id
            {_GRANT_JOIN}
            GROUP BY c.id, c.document_id, c.chunk_index, c.content, c.token_count,
                     c.metadata, d.file_name, d.source_uri
            ORDER BY score DESC
            LIMIT :candidates
            """
        )
        rows = (
            await session.execute(
                statement,
                {
                    **params,
                    "matched": json.dumps(matched),
                    "hop_decay": self._settings.graph_hop_decay,
                    "candidates": self._settings.candidates,
                },
            )
        ).mappings().all()
        return [{**dict(row), "origin": "graph"} for row in rows]

    # --- neighbour window ----------------------------------------------------

    async def _neighbors(self, session, params: dict, seeds: list[dict]) -> list[dict]:
        windows = [
            {
                "document_id": seed["document_id"],
                "chunk_index": int(seed["chunk_index"]),
                "score": float(seed["score"]),
            }
            for seed in seeds
        ]
        # Seeds already passed the grant join; neighbours share the seed's
        # document, so the grant holds for them by construction.
        statement = sql_text(
            f"""
            WITH s AS (
                SELECT * FROM jsonb_to_recordset(CAST(:windows AS jsonb))
                AS s(document_id text, chunk_index int, score double precision)
            )
            SELECT {_PROVENANCE_COLUMNS},
                   MAX(s.score) * CAST(:decay AS double precision) AS score
            FROM s
            JOIN document_chunks c ON c.document_id = s.document_id
             AND c.chunk_index BETWEEN s.chunk_index - :window AND s.chunk_index + :window
             AND c.tenant_id = :tenant_id
            JOIN documents d ON d.id = c.document_id
            GROUP BY c.id, c.document_id, c.chunk_index, c.content, c.token_count,
                     c.metadata, d.file_name, d.source_uri
            """
        )
        rows = (
            await session.execute(
                statement,
                {
                    "windows": json.dumps(windows),
                    "decay": self._settings.neighbor_decay,
                    "window": self._settings.neighbor_window,
                    "tenant_id": params["tenant_id"],
                },
            )
        ).mappings().all()
        return [{**dict(row), "origin": "neighbor"} for row in rows]

    # --- helpers -------------------------------------------------------------

    @staticmethod
    def _keep_best(merged: dict[str, dict], row: dict) -> None:
        existing = merged.get(row["chunk_id"])
        if existing is None:
            merged[row["chunk_id"]] = row
        elif float(row["score"] or 0.0) > float(existing["score"] or 0.0):
            existing["score"] = row["score"]

    @staticmethod
    def _to_chunk(row: dict) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            chunk_index=int(row["chunk_index"]),
            file_name=row["file_name"] or "",
            source_uri=row["source_uri"] or "",
            content=row["content"],
            token_count=int(row["token_count"] or 0),
            score=float(row["score"] or 0.0),
            origin=str(row.get("origin") or "dense"),
            metadata=dict(row["metadata"] or {}),
        )


_service: RetrievalService | None = None


def get_retrieval_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service
