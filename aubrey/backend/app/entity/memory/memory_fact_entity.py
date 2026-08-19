from sqlalchemy import Column, Float, Index, Text
from sqlmodel import Field

from backend.app.entity.base_models import CreatedAtModel
from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS, PgVector


class MemoryFactEntity(CreatedAtModel, table=True):
    """One stable fact about a user (semantic memory, NEW_PLAN §5).

    `content` is FieldEncryptor ciphertext at rest; the embedding is computed
    from the REDACTED plaintext before encryption, so the vector never
    encodes raw identifiers (§8.2). Rows are append-only: relevance changes
    through `weight` (decayed on a schedule, pruned below the floor), never
    through rewriting content in place."""

    __tablename__ = "memory_facts"
    __table_args__ = (
        Index("idx_memory_facts_subject", "tenant_id", "user_id", "created_at"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))  # encrypted
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(PgVector(DEFAULT_EMBEDDING_DIMENSIONS), nullable=True),
    )
    weight: float = Field(default=1.0, sa_column=Column(Float, nullable=False, default=1.0))
    source: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
