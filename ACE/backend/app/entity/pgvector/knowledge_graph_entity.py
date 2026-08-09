from typing import Any
from sqlmodel import Field
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel, UUIDModel
from backend.app.entity.pgvector.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector
from sqlalchemy import Column, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, text


class KnowledgeNodeEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "knowledge_graph_nodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "node_type",
            "canonical_name",
            name="uq_knowledge_graph_nodes_tenant_type_name",
        ),
        Index("idx_knowledge_graph_nodes_tenant_type", "tenant_id", "node_type"),
        Index("idx_knowledge_graph_nodes_name", "tenant_id", "canonical_name"),
        Index("idx_knowledge_graph_nodes_document", "document_id"),
        Index(
            "idx_knowledge_graph_nodes_properties_gin",
            "properties",
            postgresql_using="gin",
        ),
        {"comment": "GraphRAG entity nodes such as documents, sections, topics, policies, services, or teams."},
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    node_type: str = Field(sa_column=Column(Text, nullable=False))
    name: str = Field(sa_column=Column(Text, nullable=False))
    canonical_name: str = Field(sa_column=Column(Text, nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    document_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text,
            ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    embedding_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    embedding_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    embedding_dimensions: int | None = Field(
        default=DEFAULT_EMBEDDING_DIMENSIONS,
        sa_column=Column(Integer, nullable=True),
    )
    confidence_score: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    properties_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "properties",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
    )


class KnowledgeEdgeEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "src_node_id",
            "dst_node_id",
            "edge_type",
            name="uq_knowledge_graph_edges_tenant_src_dst_type",
        ),
        Index("idx_knowledge_graph_edges_src", "tenant_id", "src_node_id", "edge_type"),
        Index("idx_knowledge_graph_edges_dst", "tenant_id", "dst_node_id", "edge_type"),
        Index("idx_knowledge_graph_edges_type", "tenant_id", "edge_type"),
        Index("idx_knowledge_graph_edges_document", "document_id"),
        Index("idx_knowledge_graph_edges_source_chunk", "source_chunk_id"),
        Index(
            "idx_knowledge_graph_edges_properties_gin",
            "properties",
            postgresql_using="gin",
        ),
        {"comment": "GraphRAG relationship edges used to expand context before vector retrieval."},
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    src_node_id: str = Field(
        sa_column=Column(
            Text,
            ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    dst_node_id: str = Field(
        sa_column=Column(
            Text,
            ForeignKey("knowledge_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    edge_type: str = Field(sa_column=Column(Text, nullable=False))
    document_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text,
            ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_chunk_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text,
            ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    confidence_score: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    properties_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "properties",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
    )
