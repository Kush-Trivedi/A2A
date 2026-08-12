from sqlmodel import Field
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Column, DateTime, Index, Text
from backend.app.entity.base_models import CreatedAtModel


class BrowserSessionEntity(CreatedAtModel, table=True):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        Index("idx_browser_sessions_tenant_user", "tenant_id", "user_id"),
        Index("idx_browser_sessions_expires", "expires_at"),
    )

    session_id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str = Field(sa_column=Column(Text, nullable=False))
    actor_id: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Entra subject/oid — stable external identity.",
    )
    email: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    display_name: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    auth_provider: str = Field(
        default="entra",
        sa_column=Column(Text, nullable=False, default="entra"),
    )
    roles: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, default=list),
        description="Resolved internal role keys (Casbin subjects).",
    )
    user_profile: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, default=dict),
    )
    csrf_token_hash: str = Field(sa_column=Column(Text, nullable=False))
    ip_hash: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    user_agent_hash: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    last_seen_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
