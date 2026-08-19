from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class ConsentStatus:
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"


class SmsConsentEntity(TimestampModel, table=True):
    """The consent ledger — one row per (tenant, phone), the single gate
    every outbound SMS must pass. TCPA puts the burden of proving consent
    on the sender, so transitions are never overwritten: `history` appends
    every change with its timestamp, source and keyword."""

    __tablename__ = "sms_consent"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_sms_consent_tenant_phone"),
        Index("idx_sms_consent_tenant_status", "tenant_id", "status"),
        Index("idx_sms_consent_tenant_phone_hash", "tenant_id", "phone_hash"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    # M10-S1 searchable encryption: new writes store FieldEncryptor
    # ciphertext here and the HMAC in phone_hash (the lookup key). Legacy
    # rows keep plaintext until their next transition re-writes them.
    phone: str = Field(sa_column=Column(Text, nullable=False))  # E.164, encrypted at rest
    phone_hash: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )  # HMAC-SHA256(field key, E.164) — deterministic equality lookup
    status: str = Field(sa_column=Column(Text, nullable=False))  # opted_in | opted_out
    # admin | keyword | inbound_first_contact
    source: str = Field(sa_column=Column(Text, nullable=False))
    keyword: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    note: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    opted_in_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    opted_out_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    # append-only: [{at, from, to, source, keyword}]
    history: list[Any] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
