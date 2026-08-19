from sqlalchemy import Column, Float, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import CreatedAtModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class MemoryEpisodeEntity(CreatedAtModel, table=True):
    """One past-interaction summary (episodic memory, NEW_PLAN §5) — what a
    user worked through in an earlier session, recallable across sessions.

    Same at-rest rules as memory_facts: encrypted `content`, embedding from
    the redacted plaintext, append + weight decay, never rewritten.
    `session_id` records provenance only — recall keys on (tenant, user)."""

    __tablename__ = "memory_episodes"
    __table_args__ = (
        Index("idx_memory_episodes_subject", "tenant_id", "user_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    session_id: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    content: str = Field(sa_column=Column(Text, nullable=False))  # encrypted
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    weight: float = Field(default=1.0, sa_column=Column(Float, nullable=False, default=1.0))
    source: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
