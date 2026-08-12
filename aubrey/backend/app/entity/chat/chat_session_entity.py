from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class ChatSessionEntity(TimestampModel, table=True):
    """One conversation thread. Its id is also the A2A contextId — every
    agent (and every delegated hop) sees the same thread identity."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_owner", "tenant_id", "user_id", "archived_at"),
        Index("idx_chat_sessions_tenant_created", "tenant_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    title: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    channel: str = Field(
        default="web", sa_column=Column(Text, nullable=False, default="web")
    )  # web | sms | ... — same session model for every surface
    archived_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
