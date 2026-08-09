from dataclasses import dataclass
from typing import Any

from ...utils.common.logger import Logger
from ..agents.capability_resolver import CapabilityResolver, get_capability_resolver
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service

logger = Logger(__name__).get_logger()

_TYPE_ACCESS_DENIED = "access_denied"
_TYPE_NO_CAPABILITY = "no_capability"


@dataclass(frozen=True)
class RefusalResponse:
    type: str
    message: str
    agent_key: str = ""
    team_key: str = ""
    team_name: str = ""
    contact_email: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "message": self.message,
            "agent_key": self.agent_key,
            "team_key": self.team_key,
            "team_name": self.team_name,
            "contact_email": self.contact_email,
        }


class OutOfScopeResponder:
    """Builds the polite, actionable refusal instead of a raw 403.

    When a user asks something their roles do not cover, the answer names the
    owning ODT team and its contact so the user knows exactly where to request
    access. Never fabricates an answer, never leaks agent internals.
    """

    def __init__(
        self,
        registry_service: AgentRegistryService | None = None,
        capability_resolver: CapabilityResolver | None = None,
    ) -> None:
        self._registry = registry_service or get_agent_registry_service()
        self._capabilities = capability_resolver or get_capability_resolver()

    async def for_denied_agent(
        self, *, tenant_id: str, agent_key: str
    ) -> RefusalResponse:
        pair = await self._registry.get_agent_with_team(
            tenant_id=tenant_id, agent_key=agent_key
        )
        if pair is None:
            return RefusalResponse(
                type=_TYPE_ACCESS_DENIED,
                agent_key=agent_key,
                message=(
                    "You don't currently have access to this assistant. "
                    "Please contact your administrator to request access."
                ),
            )

        agent, team = pair
        return RefusalResponse(
            type=_TYPE_ACCESS_DENIED,
            agent_key=agent.agent_key,
            team_key=team.key,
            team_name=team.name,
            contact_email=team.contact_email or "",
            message=self._contact_message(
                subject=f"the {agent.display_name}",
                team_name=team.name,
                contact_email=team.contact_email,
            ),
        )

    async def for_missing_capability(
        self, *, tenant_id: str, capability: str
    ) -> RefusalResponse:
        match = await self._capabilities.resolve(
            tenant_id=tenant_id, capability=capability
        )
        if match is None:
            return RefusalResponse(
                type=_TYPE_NO_CAPABILITY,
                message=(
                    "No available assistant covers this topic yet. "
                    "Please contact your administrator if you believe it should be supported."
                ),
            )
        return RefusalResponse(
            type=_TYPE_ACCESS_DENIED,
            agent_key=match.agent_key,
            team_key=match.team_key,
            team_name=match.team_name,
            contact_email=match.contact_email or "",
            message=self._contact_message(
                subject=f"a question for the {match.agent_display_name}",
                team_name=match.team_name,
                contact_email=match.contact_email,
            ),
        )

    @staticmethod
    def _contact_message(
        *, subject: str, team_name: str, contact_email: str | None
    ) -> str:
        contact = f" at {contact_email}" if contact_email else ""
        return (
            f"This looks like {subject}, which your current role doesn't have "
            f"access to. Please contact the {team_name} team{contact} to request "
            "the relevant access."
        )


_responder: OutOfScopeResponder | None = None


def get_out_of_scope_responder() -> OutOfScopeResponder:
    global _responder
    if _responder is None:
        _responder = OutOfScopeResponder()
    return _responder
