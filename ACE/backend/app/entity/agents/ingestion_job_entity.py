from typing import Any
from sqlmodel import Field
from sqlalchemy import Column, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel


class IngestionJobStatus:
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    ALL = (RUNNING, COMPLETED, FAILED)


class IngestionJobEntity(TimestampModel, table=True):
    """Background bulk-ingestion jobs (source-agnostic: sharepoint, blob, ...)."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        Index("idx_ingestion_jobs_tenant", "tenant_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    source_name: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=IngestionJobStatus.RUNNING,
        sa_column=Column(Text, nullable=False, default=IngestionJobStatus.RUNNING),
    )
    detail: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
