from sqlalchemy import Column, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class SessionSummaryEntity(TimestampModel, table=True):
    """The rolling summary of one chat session — the compressed past that
    rides in the envelope when the verbatim window can't hold everything.

    One row per session (unique), REPLACED as the summary rolls forward —
    the summary is derived state, not a memory record, so the append-only
    rule does not apply to it. `summary` is FieldEncryptor ciphertext."""

    __tablename__ = "session_summaries"
    __table_args__ = (
        Index("idx_session_summaries_session", "session_id", unique=True),
        Index("idx_session_summaries_tenant", "tenant_id", "updated_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    session_id: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = Field(sa_column=Column(Text, nullable=False))  # encrypted
