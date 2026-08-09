from sqlmodel import Field
from datetime import datetime
from sqlalchemy import Column, Text, Index
from sqlalchemy import TIMESTAMP as SA_TIMESTAMP
from backend.app.entity.base_models import UUIDModel, TimestampModel

class ChatSessionEntity(UUIDModel, TimestampModel, table=True):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_owner", "tenant_id", "actor_id", "updated_at"),
        Index("idx_chat_sessions_user", "tenant_id", "user_id", "updated_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    title: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    archived_at: datetime | None = Field(default=None, sa_column=Column(SA_TIMESTAMP(timezone=True), nullable=True))
