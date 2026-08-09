from typing import Any
from datetime import datetime
from pydantic import Field
from ..base import StrictBaseModel


class RegisterTeamRequest(StrictBaseModel):
    key: str = Field(..., min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    contact_email: str | None = Field(default=None, max_length=255)


class TeamResponse(StrictBaseModel):
    id: str
    key: str
    name: str
    description: str = ""
    contact_email: str | None = None
    created_at: datetime
    updated_at: datetime


class RegisterAgentRequest(StrictBaseModel):
    team_key: str = Field(..., min_length=1, max_length=60)
    agent_key: str = Field(..., min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    card_url: str | None = Field(
        default=None,
        max_length=500,
        description="A2A AgentCard URL (the agent's /.well-known/agent-card.json).",
    )
    version: str = Field(default="0.1.0", max_length=40)
    permission: str = Field(default="chat", min_length=1, max_length=60)
    allowed_roles: list[str] = Field(
        default_factory=list,
        description="Roles granted access to this agent on registration.",
    )
    aliases: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    retrieval_mode: str | None = Field(
        default=None, description="dense | sparse | hybrid (default from config)"
    )
    team_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Team-owned configuration (e.g. Databricks warehouse_id, catalog, "
            "genie_space_id). ACE stores it, the owning team maintains it."
        ),
    )
    prompts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Team-owned prompts: name -> {version, content}. ACE records them "
            "per agent version for governance; teams author and use them."
        ),
    )


class AgentVersionModel(StrictBaseModel):
    version: str
    status: str
    display_name: str
    card_url: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    prompts: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RegisteredAgentModel(StrictBaseModel):
    id: str
    team_key: str
    agent_key: str
    display_name: str
    description: str = ""
    card_url: str | None = None
    version: str
    status: str
    permission: str
    allowed_roles: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    retrieval_mode: str | None = None
    team_config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AgentRegistrationResponse(StrictBaseModel):
    agent: RegisteredAgentModel
    policies_seeded: int


class UpdateAgentStatusRequest(StrictBaseModel):
    status: str = Field(..., pattern=r"^(registered|active|disabled)$")
