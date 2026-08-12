from typing import Any
from sqlmodel import Field
from sqlalchemy import Column, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from backend.app.entity.base_models import TimestampModel


class AgentStatus:
    REGISTERED = "registered"
    ACTIVE = "active"
    DISABLED = "disabled"

    ALL = (REGISTERED, ACTIVE, DISABLED)


class RegisteredAgentEntity(TimestampModel, table=True):
    __tablename__ = "registered_agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_key", name="uq_registered_agents_tenant_key"),
        Index("idx_registered_agents_tenant", "tenant_id"),
        Index("idx_registered_agents_team", "team_id"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    team_id: str = Field(
        sa_column=Column(Text, ForeignKey("odt_teams.id", ondelete="CASCADE"), nullable=False)
    )
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    display_name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    card_url: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    version: str = Field(default="0.1.0", sa_column=Column(Text, nullable=False, default="0.1.0"))
    status: str = Field(
        default=AgentStatus.REGISTERED,
        sa_column=Column(Text, nullable=False, default=AgentStatus.REGISTERED),
    )
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
