from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint
from backend.app.entity.base_models import TimestampModel


class TeamsConversationEntity(TimestampModel, table=True):
    """One Teams thread per (channel conversation, user) — bridged to an ACE
    chat session like SMS; any team's agent can answer."""

    __tablename__ = "teams_conversations"
    __table_args__ = (
        Index("idx_teams_conversations_lookup", "tenant_id", "conversation_hash"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    conversation_hash: str = Field(sa_column=Column(Text, nullable=False))
    teams_user_id: str = Field(sa_column=Column(Text, nullable=False))  # encrypted at rest
    user_display_name: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    chat_session_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class TeamsMessageEntity(TimestampModel, table=True):
    """Append-only Teams message log; activity id unique for retry idempotency."""

    __tablename__ = "teams_messages"
    __table_args__ = (
        UniqueConstraint("activity_id", name="uq_teams_messages_activity_id"),
        Index("idx_teams_messages_conversation", "conversation_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    conversation_id: str = Field(sa_column=Column(Text, nullable=False))
    direction: str = Field(sa_column=Column(Text, nullable=False))  # inbound | outbound
    message_body: str = Field(sa_column=Column(Text, nullable=False))  # encrypted at rest
    activity_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
