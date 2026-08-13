from datetime import datetime
from typing import Any
from sqlalchemy import Column, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimeStampedModel

class MessageFeedbackEntity(TimeStampedModel, table=True):
    __tablename__ = "message_feedback"
    __table_args__ = (
            UniqueConstraint("message_id", "created_by", name="uq_message_feedback_message_actor"),
            Index("idx_message_feedback_session_message","session_id", "message_id"),
        )

    id: int = Field(default=None, primary_key=True)
    message_id: str = Field(
        sa_column=Column(Text, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    )
    session_id: str = Field(
        sa_column=Column(Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    feedback: Any = Field(sa_column=Column(JSONB, nullable=False))
    source: str = Field(default="chat_ui",sa_column=Column(Text, nullable=False))
    feedback_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata",JSONB, nullable=True, server_default=text("'{}'::jsonb")
        )
    ) 
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True)
    )