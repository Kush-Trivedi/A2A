from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint
from backend.app.entity.base_models import TimestampModel


class SmsConversationStatus:
    ACTIVE = "active"
    OPTED_OUT = "opted_out"
    CLOSED = "closed"

    ALL = (ACTIVE, OPTED_OUT, CLOSED)


class SmsConversationEntity(TimestampModel, table=True):
    """One SMS thread per patient phone (hashed) — bridged to an ACE chat
    session so ANY team's agent can answer; only agent_key changes."""

    __tablename__ = "sms_conversations"
    __table_args__ = (
        Index("idx_sms_conversations_lookup", "tenant_id", "from_number_hash", "status"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    from_number_hash: str = Field(sa_column=Column(Text, nullable=False))
    from_number: str = Field(sa_column=Column(Text, nullable=False))  # encrypted at rest
    to_number: str = Field(sa_column=Column(Text, nullable=False))    # encrypted at rest
    status: str = Field(default=SmsConversationStatus.ACTIVE, sa_column=Column(Text, nullable=False, default=SmsConversationStatus.ACTIVE))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    chat_session_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class SmsMessageEntity(TimestampModel, table=True):
    """Append-only SMS log; twilio sid unique for webhook idempotency."""

    __tablename__ = "sms_messages"
    __table_args__ = (
        UniqueConstraint("twilio_message_sid", name="uq_sms_messages_twilio_sid"),
        Index("idx_sms_messages_conversation", "conversation_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    conversation_id: str = Field(sa_column=Column(Text, nullable=False))
    direction: str = Field(sa_column=Column(Text, nullable=False))  # inbound | outbound
    message_body: str = Field(sa_column=Column(Text, nullable=False))  # encrypted at rest
    twilio_message_sid: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    delivery_status: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
