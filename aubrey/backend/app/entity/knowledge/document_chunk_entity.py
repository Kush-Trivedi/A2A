from typing import Any

from sqlalchemy import Column, Computed, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class DocumentChunkEntity(TimestampModel, table=True):
    """One retrieval unit. Both representations are stored at ingest so the
    query side chooses dense, sparse, or hybrid later:

    - dense: `embedding` (pgvector; HNSW index added by knowledge/schema.py)
    - sparse: `search_vector` (tsvector GENERATED from the text; GIN index)

    `text_sha256` lets identical chunk text reuse an existing vector instead
    of paying the embedding API twice. `embedding_text` may carry context
    (e.g. the heading path) that is embedded but not shown to the model."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("idx_document_chunks_document", "document_id", "chunk_index"),
        Index("idx_document_chunks_tenant_created", "tenant_id", "created_at"),
        Index("idx_document_chunks_text_sha", "tenant_id", "text_sha256"),
        Index("idx_document_chunks_metadata_gin", "metadata", postgresql_using="gin"),
        Index(
            "idx_document_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    document_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        )
    )
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding_text: str = Field(sa_column=Column(Text, nullable=False))
    token_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    text_sha256: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    embedding_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
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
    chunk_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
        ),
    )
