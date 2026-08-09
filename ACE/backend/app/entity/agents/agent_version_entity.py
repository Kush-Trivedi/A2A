from typing import Any
from sqlmodel import Field
from sqlalchemy import Column, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel


class AgentVersionStatus:
    CURRENT = "current"
    SUPERSEDED = "superseded"

    ALL = (CURRENT, SUPERSEDED)


class AgentVersionEntity(TimestampModel, table=True):
    """Immutable registration snapshots — one row per agent version."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_key", "version", name="uq_agent_versions_tenant_key_ver"
        ),
        Index("idx_agent_versions_agent", "tenant_id", "agent_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    version: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=AgentVersionStatus.CURRENT,
        sa_column=Column(Text, nullable=False, default=AgentVersionStatus.CURRENT),
    )
    display_name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    card_url: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    permission: str = Field(default="chat", sa_column=Column(Text, nullable=False, default="chat"))
    retrieval_mode: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    knowledge_sources: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    allowed_roles: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    team_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    skills: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    card_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    prompts: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
