from typing import Any

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class DocumentStatus:
    CONVERTED = "converted"   # text extracted, awaiting chunk + embed
    EMBEDDED = "embedded"     # chunks in pgvector (set by the embedding step)


class DocumentEntity(TimestampModel, table=True):
    """A single source document, owned by a team + agent, deduplicated by
    content hash within that owner scope."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_tenant_created", "tenant_id", "created_at"),
        Index("idx_documents_owner", "tenant_id", "team_key", "agent_key"),
        Index("idx_documents_sha256", "tenant_id", "agent_key", "sha256"),
        Index("idx_documents_batch", "batch_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    batch_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text, ForeignKey("document_batches.id", ondelete="SET NULL"), nullable=True
        ),
    )
    source_type: str = Field(sa_column=Column(Text, nullable=False))  # sharepoint | blob
    file_name: str = Field(sa_column=Column(Text, nullable=False))
    source_uri: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sha256: str = Field(sa_column=Column(Text, nullable=False))
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    status: str = Field(
        default=DocumentStatus.CONVERTED,
        sa_column=Column(Text, nullable=False, default=DocumentStatus.CONVERTED),
    )
    chunk_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    doc_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
        ),
    )
