"""GraphRAG extraction: entities + relationships per chunk, deduplicated by
construction.

The chat model returns strict JSON per chunk. Entity names normalize to a
canonical form and upsert against the (tenant, node_type, canonical_name)
unique key — 'Dr. Smith' across ten documents is ONE node whose
mention_count grows, never ten rows. Relationships upsert the same way per
(tenant, src, dst, edge_type). Every mention links chunk ↔ node; that link
table is what 1-hop retrieval walks later. New nodes get their own
embedding so a query can match entities semantically.

A chunk whose extraction fails (bad JSON, LLM error) is logged and skipped:
the graph is additive context on top of vector search, and a failed
extraction must not fail the document's ingestion."""

import json
import re
import uuid

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.knowledge import (
    ChunkEntityMentionEntity,
    KnowledgeEdgeEntity,
    KnowledgeNodeEntity,
)
from ...llm.azure_foundry import AceAzureFoundry
from ...utils.common.logger import Logger
from .embedding_service import EmbeddingService, get_embedding_service

logger = Logger(__name__).get_logger()

_EXTRACTION_INSTRUCTIONS = """You extract a knowledge graph from text.
Return ONLY a JSON object, no prose, in exactly this shape:
{"entities": [{"name": "...", "type": "...", "description": "..."}],
 "relations": [{"source": "...", "target": "...", "type": "...", "description": "..."}]}

Rules:
- Entities are specific named things: people, organizations, teams, services,
  medications, conditions, procedures, policies, systems, locations, concepts.
- "type" is one short lowercase word (e.g. person, organization, medication).
- "description" is one sentence grounded in the text; never invent facts.
- Relation "type" is a short UPPER_SNAKE verb phrase (e.g. TREATS, WORKS_FOR,
  PART_OF, DEPENDS_ON); source/target must exactly match an entity "name".
- Prefer fewer, high-value entities over exhaustive lists. Skip dates,
  numbers, and generic words. Return {"entities": [], "relations": []} when
  nothing qualifies."""

_CANONICAL_STRIP_RE = re.compile(r"[^a-z0-9 ]+")


def canonicalize(name: str) -> str:
    """Normalized identity for dedup: lowercase, punctuation-free,
    whitespace-collapsed."""
    lowered = _CANONICAL_STRIP_RE.sub(" ", (name or "").strip().lower())
    return " ".join(lowered.split())


class GraphExtractionService:
    def __init__(
        self,
        foundry: AceAzureFoundry | None = None,
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self._foundry = foundry or AceAzureFoundry()
        self._embeddings = embeddings or get_embedding_service()
        self._db = get_postgres_connector()

    async def extract_chunk(
        self, *, tenant_id: str, chunk_id: str, text: str
    ) -> int:
        """Extract + upsert the graph for one chunk. Returns the number of
        entities linked; 0 (with a warning) when extraction fails."""
        try:
            raw = await self._foundry.acomplete_chat(
                messages=[
                    {"role": "system", "content": _EXTRACTION_INSTRUCTIONS},
                    {"role": "user", "content": text},
                ]
            )
            payload = self._parse(raw)
        except Exception:  # noqa: BLE001 — graph is additive; log and move on
            logger.warning(
                "Graph extraction failed for chunk; continuing without it",
                extra={"chunk_id": chunk_id},
                exc_info=True,
            )
            return 0

        entities = payload.get("entities") or []
        relations = payload.get("relations") or []
        if not entities:
            return 0

        node_ids = await self._upsert_nodes(tenant_id, chunk_id, entities)
        await self._upsert_edges(tenant_id, chunk_id, relations, node_ids)
        return len(node_ids)

    @staticmethod
    def _parse(raw: str) -> dict:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("extraction response contained no JSON object")
        return json.loads(text[start : end + 1])

    async def _upsert_nodes(
        self, tenant_id: str, chunk_id: str, entities: list[dict]
    ) -> dict[str, str]:
        """Upsert each entity; link the chunk. Returns canonical_name → node id."""
        node_ids: dict[str, str] = {}
        new_nodes: list[KnowledgeNodeEntity] = []

        async with self._db.session() as session:
            for entity in entities:
                name = str(entity.get("name") or "").strip()
                node_type = canonicalize(str(entity.get("type") or "concept")) or "concept"
                canonical = canonicalize(name)
                if not canonical or canonical in node_ids:
                    continue
                description = str(entity.get("description") or "").strip()

                node = (
                    await session.exec(
                        select(KnowledgeNodeEntity).where(
                            KnowledgeNodeEntity.tenant_id == tenant_id,
                            KnowledgeNodeEntity.node_type == node_type,
                            KnowledgeNodeEntity.canonical_name == canonical,
                        )
                    )
                ).first()
                if node is not None:
                    node.mention_count += 1
                    if description and len(description) > len(node.description):
                        node.description = description
                    session.add(node)
                else:
                    node = KnowledgeNodeEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        node_type=node_type,
                        name=name,
                        canonical_name=canonical,
                        description=description,
                    )
                    session.add(node)
                    new_nodes.append(node)
                node_ids[canonical] = node.id

            # The session flushes on commit in add-order, so node INSERTs must
            # reach the database before mention rows reference their ids.
            await session.flush()

            for node_id in node_ids.values():
                mention = (
                    await session.exec(
                        select(ChunkEntityMentionEntity).where(
                            ChunkEntityMentionEntity.chunk_id == chunk_id,
                            ChunkEntityMentionEntity.node_id == node_id,
                        )
                    )
                ).first()
                if mention is None:
                    session.add(
                        ChunkEntityMentionEntity(
                            id=uuid.uuid4().hex,
                            tenant_id=tenant_id,
                            chunk_id=chunk_id,
                            node_id=node_id,
                        )
                    )

        if new_nodes:
            await self._embed_nodes(new_nodes)
        return node_ids

    async def _embed_nodes(self, nodes: list[KnowledgeNodeEntity]) -> None:
        texts = [
            f"{n.node_type}: {n.name}" + (f" — {n.description}" if n.description else "")
            for n in nodes
        ]
        vectors = await self._embeddings.embed(texts)
        async with self._db.session() as session:
            for node, vector in zip(nodes, vectors):
                row = (
                    await session.exec(
                        select(KnowledgeNodeEntity).where(
                            KnowledgeNodeEntity.id == node.id
                        )
                    )
                ).one()
                row.embedding = vector
                row.embedding_model = self._embeddings.model_name
                session.add(row)

    async def _upsert_edges(
        self,
        tenant_id: str,
        chunk_id: str,
        relations: list[dict],
        node_ids: dict[str, str],
    ) -> None:
        async with self._db.session() as session:
            for relation in relations:
                src = node_ids.get(canonicalize(str(relation.get("source") or "")))
                dst = node_ids.get(canonicalize(str(relation.get("target") or "")))
                edge_type = (
                    str(relation.get("type") or "").strip().upper().replace(" ", "_")
                )
                if not src or not dst or not edge_type or src == dst:
                    continue
                edge = (
                    await session.exec(
                        select(KnowledgeEdgeEntity).where(
                            KnowledgeEdgeEntity.tenant_id == tenant_id,
                            KnowledgeEdgeEntity.src_node_id == src,
                            KnowledgeEdgeEntity.dst_node_id == dst,
                            KnowledgeEdgeEntity.edge_type == edge_type,
                        )
                    )
                ).first()
                if edge is not None:
                    edge.evidence_count += 1
                    session.add(edge)
                    continue
                session.add(
                    KnowledgeEdgeEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        src_node_id=src,
                        dst_node_id=dst,
                        edge_type=edge_type,
                        description=str(relation.get("description") or "").strip(),
                        source_chunk_id=chunk_id,
                    )
                )


_service: GraphExtractionService | None = None


def get_graph_extraction_service() -> GraphExtractionService:
    global _service
    if _service is None:
        _service = GraphExtractionService()
    return _service
