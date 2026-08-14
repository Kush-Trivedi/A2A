from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class GenieConversationEntity(TimestampModel, table=True):
    """Maps (chat session, connection) to a Genie conversation, so
    follow-up questions continue the same Genie thread (accuracy) while
    agents stay completely stateless — the platform owns the mapping.
    One Genie conversation per user session, never shared across users."""

    __tablename__ = "genie_conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "session_id", "connection_key",
            name="uq_genie_conversations_session_connection",
        ),
        Index("idx_genie_conversations_tenant_session", "tenant_id", "session_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    session_id: str = Field(sa_column=Column(Text, nullable=False))
    connection_key: str = Field(sa_column=Column(Text, nullable=False))
    space_id: str = Field(sa_column=Column(Text, nullable=False))
    conversation_id: str = Field(sa_column=Column(Text, nullable=False))
