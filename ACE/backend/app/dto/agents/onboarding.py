from pydantic import Field

from ..base import StrictBaseModel


class TeamTokenResponse(StrictBaseModel):
    team_key: str
    token: str  # shown exactly once — only the hash is stored


class RouteOverlapModel(StrictBaseModel):
    agent_key: str
    utterance: str
    score: float


class OnboardingRegistrationResponse(StrictBaseModel):
    agent_key: str
    team_key: str
    status: str
    policies_seeded: int
    route_utterances: int = 0
    route_overlaps: list[RouteOverlapModel] = Field(default_factory=list)
