from datetime import datetime, timezone

from sqlalchemy import Column, Index, Integer, Text
from sqlmodel import TIMESTAMP, Field, SQLModel


class DeletionEvidenceEntity(SQLModel, table=True):
    """The auditable answer to "prove you deleted it" (NEW_PLAN §8.3/§9.5):
    every retention purge, body overwrite, and right-to-erasure action
    writes one row — what class of data, which subject/table, how many
    rows, when. Metadata only, NEVER content or raw identifiers: `target`
    carries a table name or a hashed subject token, so the evidence trail
    cannot itself become the leak."""

    __tablename__ = "deletion_evidence"
    __table_args__ = (
        Index("idx_deletion_evidence_tenant", "tenant_id", "executed_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    # retention_purge | retention_overwrite | erasure | prospects_cancelled
    action: str = Field(sa_column=Column(Text, nullable=False))
    # table name or hashed subject token — never a raw identifier
    target: str = Field(sa_column=Column(Text, nullable=False))
    count: int = Field(sa_column=Column(Integer, nullable=False))
    executed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_type=TIMESTAMP(timezone=True),
    )
