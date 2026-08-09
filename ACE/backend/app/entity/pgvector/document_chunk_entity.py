from typing import Any
from sqlmodel import Field
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from backend.app.entity.base_models import TimestampModel, UUIDModel
from sqlalchemy import Column, Computed, ForeignKey, Index, Integer, Text, text
from backend.app.entity.pgvector.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class DocumentChunkEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("idx_document_chunks_document", "document_id", "chunk_index"),
        Index("idx_document_chunks_tenant_created", "tenant_id", "created_at"),
        Index("idx_document_chunks_node", "tenant_id", "node_id"),
        Index(
            "idx_document_chunks_metadata_gin",
            "metadata",
            postgresql_using="gin",
        ),
        Index(
            "idx_document_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        {"comment": "Document chunks prepared for pgvector semantic search and Postgres full-text search."},
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    document_id: str = Field(
        sa_column=Column(
            Text,
            ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    node_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text,
            ForeignKey("knowledge_graph_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding_text: str = Field(sa_column=Column(Text, nullable=False))
    token_count_estimate: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    embedding_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    embedding_dimensions: int | None = Field(
        default=DEFAULT_EMBEDDING_DIMENSIONS,
        sa_column=Column(Integer, nullable=True),
    )
    search_vector: str | None = Field(
        default=None,
        sa_column=Column(
            TSVECTOR,
            Computed(
                "to_tsvector('english', coalesce(embedding_text, content))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
    )
