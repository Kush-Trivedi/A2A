from datetime import datetime

from ..base import StrictBaseModel


class RegisterTeamRequest(StrictBaseModel):
    key: str
    name: str
    description: str = ""
    contact_email: str | None = None


class TeamModel(StrictBaseModel):
    id: str
    key: str
    name: str
    description: str
    contact_email: str | None
    created_at: datetime
    updated_at: datetime


class RegisterAgentRequest(StrictBaseModel):
    team_key: str
    agent_key: str
    display_name: str
    description: str = ""
    card_url: str | None = None
    version: str = "0.1.0"
    permission: str = "chat"
    allowed_roles: list[str] = []


class AgentModel(StrictBaseModel):
    id: str
    agent_key: str
    display_name: str
    description: str
    card_url: str | None
    version: str
    status: str
    permission: str
    allowed_roles: list[str]
    created_at: datetime
    updated_at: datetime


class AgentRegistrationResponse(StrictBaseModel):
    agent: AgentModel
    policies_seeded: int


class UpdateAgentStatusRequest(StrictBaseModel):
    status: str


class TeamTokenResponse(StrictBaseModel):
    team_key: str
    token: str  # shown exactly once — only the hash is stored
