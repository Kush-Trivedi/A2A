from typing import Any

from sqlalchemy import Column, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class MessageRole:
    USER = "user"
    ASSISTANT = "assistant"


class MessageKind:
    """What produced an assistant message — data, not magic agent keys.
    Stickiness follows the last ANSWER's agent_key; routing messages
    (disambiguation, refusals) never capture stickiness."""

    ANSWER = "answer"
    ROUTING = "routing"


class ChatMessageEntity(TimestampModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_messages_session_created", "session_id", "created_at"),
        Index("idx_chat_messages_tenant_created", "tenant_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    session_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        )
    )
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(sa_column=Column(Text, nullable=False))
    role: str = Field(sa_column=Column(Text, nullable=False))  # user | assistant
    content: str = Field(sa_column=Column(Text, nullable=False))
    # assistant: {kind, agent_key?, sources?[], task_id?, ...} — JSONB, queryable
    message_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
        ),
    )
