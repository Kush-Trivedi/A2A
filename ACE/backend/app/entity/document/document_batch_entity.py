from typing import Any
from sqlmodel import Field
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Column, Text, Index, Integer, text
from backend.app.entity.base_models import TimestampModel, UUIDModel

class DocumentBatchEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "document_batches"
    __table_args__ = (
        Index("idx_document_batches_tenant_created", "tenant_id", "created_at"),
        Index("idx_document_batches_actor", "tenant_id", "actor_id", "created_at"),
        {"comment": "A batch of documents uploaded or processed together."},
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    batch_name: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default="processing", sa_column=Column(Text, nullable=False))
    document_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    processed_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    skipped_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    failed_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    property_json: dict[str, Any] = Field(
        default_factory={}, 
        sa_column=Column(
            "properties",
            JSONB, 
            nullable=False, server_default=text("'{}'::jsonb")
        )
    )