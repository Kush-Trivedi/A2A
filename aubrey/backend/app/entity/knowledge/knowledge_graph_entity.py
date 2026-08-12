from typing import Any

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class KnowledgeNodeEntity(TimestampModel, table=True):
    """A GraphRAG entity (person, medication, policy, team, …), stored ONCE
    per tenant: `canonical_name` normalizes every surface form and the
    unique constraint makes duplicates impossible by construction — a new
    mention of an existing entity only bumps `mention_count` and links the
    chunk. Nodes carry their own embedding so a query can match entities
    semantically before walking edges (1-hop expansion at retrieval)."""

    __tablename__ = "knowledge_graph_nodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "node_type", "canonical_name",
            name="uq_knowledge_graph_nodes_identity",
        ),
        Index("idx_knowledge_graph_nodes_tenant_type", "tenant_id", "node_type"),
        Index("idx_knowledge_graph_nodes_name", "tenant_id", "canonical_name"),
        Index(
            "idx_knowledge_graph_nodes_properties_gin",
            "properties",
            postgresql_using="gin",
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    node_type: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    canonical_name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    mention_count: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    embedding_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    properties: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )


class KnowledgeEdgeEntity(TimestampModel, table=True):
    """A relationship between two entities, unique per (tenant, src, dst,
    type) — re-observing the same relationship bumps `evidence_count`
    instead of duplicating the edge."""

    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "src_node_id", "dst_node_id", "edge_type",
            name="uq_knowledge_graph_edges_identity",
        ),
        Index("idx_knowledge_graph_edges_src", "tenant_id", "src_node_id", "edge_type"),
        Index("idx_knowledge_graph_edges_dst", "tenant_id", "dst_node_id", "edge_type"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    src_node_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=False
        )
    )
    dst_node_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=False
        )
    )
    edge_type: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    evidence_count: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    confidence: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    source_chunk_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text, ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
        ),
    )


class ChunkEntityMentionEntity(TimestampModel, table=True):
    """Chunk ↔ entity link: which chunks mention which graph nodes. This is
    the bridge 1-hop retrieval walks — entity match → edges → mentions →
    chunks (grant-filtered on the way out)."""

    __tablename__ = "chunk_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "node_id", name="uq_chunk_entity_mentions_pair"
        ),
        Index("idx_chunk_entity_mentions_node", "tenant_id", "node_id"),
        Index("idx_chunk_entity_mentions_chunk", "chunk_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    chunk_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False
        )
    )
    node_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"), nullable=False
        )
    )
