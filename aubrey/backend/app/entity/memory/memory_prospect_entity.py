from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import CreatedAtModel


class ProspectStatus:
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class MemoryProspectEntity(CreatedAtModel, table=True):
    """One future commitment (prospective memory, NEW_PLAN §2 row 7) —
    "remind me", "follow up after results", a campaign follow-up — surfaced
    when a session opens near its due date.

    `content` is FieldEncryptor ciphertext at rest (§8.2); no embedding —
    recall is by due-window, not similarity. Rows are never rewritten:
    state changes through `status` (open -> done | cancelled), and stale
    open prospects are cancelled by the decay sweep, not deleted, so the
    trail stays auditable. `due_at` may be NULL (promised, but undated).
    `source_session` records provenance only — recall keys on
    (tenant, user, status, due_at)."""

    __tablename__ = "memory_prospects"
    __table_args__ = (
        Index("idx_memory_prospects_subject", "tenant_id", "user_id", "status", "due_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))  # encrypted
    due_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    status: str = Field(
        default=ProspectStatus.OPEN,
        sa_column=Column(Text, nullable=False, default=ProspectStatus.OPEN),
    )  # open | done | cancelled
    source_session: str = Field(
        default="", sa_column=Column(Text, nullable=False, default="")
    )
