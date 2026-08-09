from sqlmodel import Field
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Text
from backend.app.entity.base_models import TimestampModel, UUIDModel


class ChatFileAttachmentEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "chat_file_attachments"
    __table_args__ = (
        Index("idx_chat_file_attachments_session", "session_id", "created_at"),
        Index("idx_chat_file_attachments_message", "message_id"),
        Index("idx_chat_file_attachments_actor", "tenant_id", "actor_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    session_id: str = Field(sa_column=Column(Text, ForeignKey("chat_sessions.id"), nullable=False))
    message_id: str | None = Field(
        default=None,
        sa_column=Column(Text, ForeignKey("chat_messages.id"), nullable=True),
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    file_name: str = Field(sa_column=Column(Text, nullable=False))
    mime_type: str = Field(sa_column=Column(Text, nullable=False))
    size_bytes: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    storage_uri: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    checksum_sha256: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    status: str = Field(default="uploaded", sa_column=Column(Text, nullable=False))
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
