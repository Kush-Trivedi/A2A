from dataclasses import dataclass

from ...security.authorization.context_attrs import AuthorizationContextBuilder
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from .registry_service import AgentRegistryService, get_agent_registry_service

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class CatalogAgent:
    id: str
    display_name: str
    description: str
    team_key: str = ""
    is_remote: bool = False


class AgentCatalogService:
    def __init__(
        self,
        registry_service: AgentRegistryService | None = None,
        enforcer: CasbinEnforcer | None = None,
    ) -> None:
        self._registry = registry_service or get_agent_registry_service()
        self._enforcer = enforcer or get_casbin_enforcer()

    async def list_for(self, context: SessionContext) -> list[CatalogAgent]:
        catalog: list[CatalogAgent] = []

        pairs = await self._registry.list_active_agents_with_teams(
            tenant_id=context.tenant_id
        )
        for agent, team in pairs:
            if await self._allowed(context, agent.agent_key, agent.permission):
                catalog.append(
                    CatalogAgent(
                        id=agent.agent_key,
                        display_name=agent.display_name,
                        description=agent.description,
                        team_key=team.key,
                        is_remote=True,
                    )
                )
        return catalog

    async def _allowed(
        self, context: SessionContext, agent_key: str, permission: str | None
    ) -> bool:
        if not permission or not self._enforcer.enabled:
            return True
        if not context.roles:
            return False
        return await self._enforcer.enforce_any_role(
            context.roles,
            context.tenant_id,
            f"agent:{agent_key}",
            permission,
            AuthorizationContextBuilder.build(context),
        )


_service: AgentCatalogService | None = None


def get_agent_catalog_service() -> AgentCatalogService:
    global _service
    if _service is None:
        _service = AgentCatalogService()
    return _service
