from dataclasses import dataclass
from ...utils.common.logger import Logger
from .registry_service import AgentRegistryService, get_agent_registry_service

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class CapabilityMatch:
    agent_key: str
    agent_display_name: str
    skill_id: str
    skill_name: str
    team_key: str
    team_name: str
    contact_email: str | None


class CapabilityResolver:
    def __init__(self, registry_service: AgentRegistryService | None = None) -> None:
        self._registry = registry_service or get_agent_registry_service()

    async def resolve(
        self, *, tenant_id: str, capability: str
    ) -> CapabilityMatch | None:
        normalized = (capability or "").strip().lower()
        if not normalized:
            return None

        pairs = await self._registry.list_active_agents_with_teams(tenant_id=tenant_id)

        for matcher in (self._match_skill_id, self._match_skill_tag, self._match_agent_key):
            for agent, team in pairs:
                match = matcher(normalized, agent, team)
                if match is not None:
                    return match
        return None

    @staticmethod
    def _build(agent, team, skill: dict) -> CapabilityMatch:
        return CapabilityMatch(
            agent_key=agent.agent_key,
            agent_display_name=agent.display_name,
            skill_id=str(skill.get("id", "")),
            skill_name=str(skill.get("name", "")),
            team_key=team.key,
            team_name=team.name,
            contact_email=team.contact_email,
        )

    @classmethod
    def _match_skill_id(cls, capability: str, agent, team) -> CapabilityMatch | None:
        for skill in agent.skills or []:
            if str(skill.get("id", "")).strip().lower() == capability:
                return cls._build(agent, team, skill)
        return None

    @classmethod
    def _match_skill_tag(cls, capability: str, agent, team) -> CapabilityMatch | None:
        for skill in agent.skills or []:
            tags = [str(tag).strip().lower() for tag in skill.get("tags", [])]
            if capability in tags:
                return cls._build(agent, team, skill)
        return None

    @classmethod
    def _match_agent_key(cls, capability: str, agent, team) -> CapabilityMatch | None:
        keys = {agent.agent_key.strip().lower()}
        keys.update(str(alias).strip().lower() for alias in agent.aliases or [])
        if capability in keys:
            first_skill = (agent.skills or [{}])[0]
            return cls._build(agent, team, first_skill)
        return None


_resolver: CapabilityResolver | None = None


def get_capability_resolver() -> CapabilityResolver:
    global _resolver
    if _resolver is None:
        _resolver = CapabilityResolver()
    return _resolver
