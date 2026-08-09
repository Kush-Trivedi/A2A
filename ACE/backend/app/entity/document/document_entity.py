from typing import Any
from sqlmodel import Field
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel, UUIDModel
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Text, text


class DocumentSourceType:
    SHAREPOINT = "sharepoint"
    AZURE_BLOB = "azure_blob"
    UPLOAD = "upload"
    SESSION_UPLOAD = "session_upload"
    TEXT = "text"


class DocumentEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_tenant_created", "tenant_id", "created_at"),
        Index("idx_documents_batch", "batch_id", "created_at"),
        Index("idx_documents_sha256", "tenant_id", "sha256"),
        Index("idx_documents_status", "tenant_id", "status"),
        Index("idx_documents_source_type", "tenant_id", "source_type"),
        Index(
            "idx_documents_metadata_gin",
            "metadata",
            postgresql_using="gin",
        ),
        Index(
            "idx_documents_source_metadata_gin",
            "source_metadata",
            postgresql_using="gin",
        ),
        {"comment": "Source-agnostic document record (SharePoint, storage account, or any future source)."},
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    batch_id: str | None = Field(
        default=None,
        sa_column=Column(
            Text,
            ForeignKey("document_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    source_type: str = Field(sa_column=Column(Text, nullable=False))
    source_uri: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Canonical, source-addressable location (e.g. SharePoint webUrl or blob URL).",
    )
    source_metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "source_metadata",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
        description=(
            "Source-specific identifiers, e.g. SharePoint: site_id, site_path, drive_id, "
            "drive_name, item_id, etag. Azure Blob: storage_account_url, container, blob_name, "
            "version_id."
        ),
    )
    source_name: str = Field(sa_column=Column(Text, nullable=False))
    content_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    detected_mime_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    detected_extension: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default="processing", sa_column=Column(Text, nullable=False))
    sha256: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    size_bytes: int | None = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    chunk_count: int | None = Field(default=0, sa_column=Column(Integer, nullable=False))
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
        ),
    )