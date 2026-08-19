from typing import Any

from sqlalchemy import Column, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class SmsDirection:
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SmsMessageEntity(TimestampModel, table=True):
    """Every SMS in or out, with its full Twilio lifecycle. `status` is the
    latest known state (queued → sending → sent → delivered / undelivered /
    failed); `status_history` appends every callback so the whole journey
    is auditable; `error_code`/`error_explanation` capture why a message
    died (30003 unreachable, 30007 carrier filtered, 21610 opted out, ...)."""

    __tablename__ = "sms_messages"
    __table_args__ = (
        Index("idx_sms_messages_sid", "twilio_sid"),
        Index("idx_sms_messages_tenant_phone", "tenant_id", "phone", "created_at"),
        Index(
            "idx_sms_messages_tenant_phone_hash", "tenant_id", "phone_hash", "created_at"
        ),
        Index("idx_sms_messages_tenant_campaign", "tenant_id", "campaign_key", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    # M10-S1: new rows store ciphertext here; phone_hash is the lookup key.
    phone: str = Field(sa_column=Column(Text, nullable=False))  # remote party E.164, encrypted at rest
    phone_hash: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )  # HMAC-SHA256(field key, E.164)
    direction: str = Field(sa_column=Column(Text, nullable=False))  # inbound | outbound
    campaign_key: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    agent_key: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    session_id: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    twilio_sid: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    body: str = Field(sa_column=Column(Text, nullable=False))  # encrypted at rest (M10-S1)
    status: str = Field(sa_column=Column(Text, nullable=False))
    error_code: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    error_explanation: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )
    # full ErrorMessage text from the status callback, when Twilio sends one
    error_message: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    num_segments: int | None = Field(default=None)
    num_media: int = Field(default=0)
    # STOP | HELP | START — set when the inbound was a compliance keyword
    opt_out_type: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    # raw webhook details worth keeping (SmsStatus, To/FromCountry, AccountSid, ...)
    vendor_details: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    # append-only: [{status, error_code, at}]
    status_history: list[Any] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
