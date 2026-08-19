from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class SmsThreadEntity(TimestampModel, table=True):
    """Maps (phone, campaign) to a chat session — the same session model
    every surface uses (channel='sms'), so memory windows, message history
    and future delegation work unchanged for SMS."""

    __tablename__ = "sms_threads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "phone", "campaign_key", name="uq_sms_threads_phone_campaign"
        ),
        Index("idx_sms_threads_tenant_phone", "tenant_id", "phone"),
        Index("idx_sms_threads_tenant_phone_hash", "tenant_id", "phone_hash"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    # M10-S1: new rows store ciphertext here; phone_hash is the lookup key.
    phone: str = Field(sa_column=Column(Text, nullable=False))  # E.164, encrypted at rest
    phone_hash: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )  # HMAC-SHA256(field key, E.164)
    campaign_key: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    session_id: str = Field(
        sa_column=Column(
            Text, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
        )
    )
    last_outbound_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_inbound_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
