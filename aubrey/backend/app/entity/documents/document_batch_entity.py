from typing import Any

from sqlalchemy import Column, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class BatchStatus:
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"


class DocumentBatchEntity(TimestampModel, table=True):
    """One ingestion run. Every batch belongs to a TEAM and a DEDICATED
    AGENT — documents inherit that ownership. Counts update live while the
    run processes."""

    __tablename__ = "document_batches"
    __table_args__ = (
        Index("idx_document_batches_tenant_created", "tenant_id", "created_at"),
        Index("idx_document_batches_owner", "tenant_id", "team_key", "agent_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    source_type: str = Field(sa_column=Column(Text, nullable=False))  # sharepoint | blob
    batch_name: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=BatchStatus.PROCESSING,
        sa_column=Column(Text, nullable=False, default=BatchStatus.PROCESSING),
    )
    document_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    processed_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    # content already existed — this run only granted it to the batch's agent
    linked_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    skipped_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    failed_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    properties: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
