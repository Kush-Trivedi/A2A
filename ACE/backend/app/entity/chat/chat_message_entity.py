from sqlmodel import Field
from sqlalchemy import Boolean, Column, ForeignKey, Index, Text
from backend.app.entity.base_models import UUIDModel, CreatedAtModel


class ChatMessageEntity(UUIDModel, CreatedAtModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_messages_session", "session_id", "created_at"),
        Index("idx_chat_messages_active", "session_id", "is_superseded", "created_at"),
        Index("idx_chat_messages_user_session", "tenant_id", "user_id", "session_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    session_id: str = Field(sa_column=Column(Text, ForeignKey("chat_sessions.id"), nullable=False))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    role: str = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    feedback: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_superseded: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
    edit_chain_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    edit_version_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
