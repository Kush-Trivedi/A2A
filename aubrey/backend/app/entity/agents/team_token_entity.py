from datetime import datetime

from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint, Boolean, TIMESTAMP
from backend.app.entity.base_models import CreatedAtModel


class TeamTokenEntity(CreatedAtModel, table=True):
    """Team registration tokens: admin-issued, Casbin-scoped by team.

    Agents self-register with `Authorization: Bearer <token>` — headless in
    CI/CD and on startup. Only the SHA-256 hash is stored; the raw token is
    shown exactly once at issue time.
    """

    __tablename__ = "team_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_team_tokens_hash"),
        Index("idx_team_tokens_tenant_team", "tenant_id", "team_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_key: str = Field(sa_column=Column(Text, nullable=False))
    token_hash: str = Field(sa_column=Column(Text, nullable=False))
    label: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    revoked: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False))
    last_used_at: datetime | None = Field(
        default=None, sa_column=Column(TIMESTAMP(timezone=True), nullable=True)
    )
