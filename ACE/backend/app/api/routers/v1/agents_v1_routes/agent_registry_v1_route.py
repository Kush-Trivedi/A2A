from fastapi import APIRouter, Depends, status

from .....dto.agents import (
    AgentRegistrationResponse,
    AgentVersionModel,
    RegisterAgentRequest,
    RegisteredAgentModel,
    RegisterTeamRequest,
    TeamResponse,
    UpdateAgentStatusRequest,
)
from .....dto.common import ApiEnvelope
from .....entity.agents import OdtTeamEntity, RegisteredAgentEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.agents.registry_service import AgentRegistryService
from ....dependencies import provide_agent_registry_service

agent_registry_v1_router = APIRouter(prefix="/admin/agents", tags=["Admin / Agent Registry"])

_REGISTRY_OBJ = "/api/v1/admin/agents"


def _to_team(team: OdtTeamEntity) -> TeamResponse:
    return TeamResponse(
        id=team.id,
        key=team.key,
        name=team.name,
        description=team.description,
        contact_email=team.contact_email,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def _to_agent(agent: RegisteredAgentEntity, team_key: str) -> RegisteredAgentModel:
    return RegisteredAgentModel(
        id=agent.id,
        team_key=team_key,
        agent_key=agent.agent_key,
        display_name=agent.display_name,
        description=agent.description,
        card_url=agent.card_url,
        version=agent.version,
        status=agent.status,
        permission=agent.permission,
        allowed_roles=list(agent.allowed_roles or []),
        aliases=list(agent.aliases or []),
        knowledge_sources=list(agent.knowledge_sources or []),
        retrieval_mode=agent.retrieval_mode,
        team_config=dict(agent.team_config or {}),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@agent_registry_v1_router.post(
    "/teams",
    response_model=ApiEnvelope[TeamResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_REGISTRY_OBJ, "POST")),
    ],
)
async def register_team(
    body: RegisterTeamRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[TeamResponse]:
    team = await service.register_team(
        context=context,
        key=body.key,
        name=body.name,
        description=body.description,
        contact_email=body.contact_email,
    )
    return ApiEnvelope(data=_to_team(team), message="Team registered.")


@agent_registry_v1_router.get(
    "/teams",
    response_model=ApiEnvelope[list[TeamResponse]],
    dependencies=[Depends(require_permission(_REGISTRY_OBJ, "GET"))],
)
async def list_teams(
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[list[TeamResponse]]:
    teams = await service.list_teams(context=context)
    return ApiEnvelope(data=[_to_team(t) for t in teams])


@agent_registry_v1_router.post(
    "",
    response_model=ApiEnvelope[AgentRegistrationResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_REGISTRY_OBJ, "POST")),
    ],
)
async def register_agent(
    body: RegisterAgentRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[AgentRegistrationResponse]:
    agent, team_key, policies_seeded = await service.register_agent(
        context=context,
        team_key=body.team_key,
        agent_key=body.agent_key,
        display_name=body.display_name,
        description=body.description,
        card_url=body.card_url,
        version=body.version,
        permission=body.permission,
        allowed_roles=body.allowed_roles,
        aliases=body.aliases,
        knowledge_sources=body.knowledge_sources,
        retrieval_mode=body.retrieval_mode,
        team_config=body.team_config,
        prompts=body.prompts,
    )
    return ApiEnvelope(
        data=AgentRegistrationResponse(
            agent=_to_agent(agent, team_key),
            policies_seeded=policies_seeded,
        ),
        message="Agent registered.",
    )


@agent_registry_v1_router.get(
    "",
    response_model=ApiEnvelope[list[RegisteredAgentModel]],
    dependencies=[Depends(require_permission(_REGISTRY_OBJ, "GET"))],
)
async def list_agents(
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[list[RegisteredAgentModel]]:
    agents = await service.list_agents(context=context)
    return ApiEnvelope(data=[_to_agent(agent, team_key) for agent, team_key in agents])


@agent_registry_v1_router.get(
    "/{agent_key}/versions",
    response_model=ApiEnvelope[list[AgentVersionModel]],
    dependencies=[Depends(require_permission(_REGISTRY_OBJ, "GET"))],
)
async def list_agent_versions(
    agent_key: str,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[list[AgentVersionModel]]:
    versions = await service.list_agent_versions(
        tenant_id=context.tenant_id, agent_key=agent_key
    )
    return ApiEnvelope(
        data=[
            AgentVersionModel(
                version=v.version,
                status=v.status,
                display_name=v.display_name,
                card_url=v.card_url,
                allowed_roles=list(v.allowed_roles or []),
                knowledge_sources=list(v.knowledge_sources or []),
                prompts=dict(v.prompts or {}),
                created_at=v.created_at,
                updated_at=v.updated_at,
            )
            for v in versions
        ]
    )


@agent_registry_v1_router.post(
    "/{agent_key}/versions/{version}/activate",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_REGISTRY_OBJ, "POST")),
    ],
)
async def activate_agent_version(
    agent_key: str,
    version: str,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[dict]:
    agent = await service.activate_version(
        context=context, agent_key=agent_key, version=version
    )
    return ApiEnvelope(
        data={"agent_key": agent.agent_key, "active_version": agent.version},
        message="Version activated.",
    )


@agent_registry_v1_router.patch(
    "/{agent_key}/status",
    response_model=ApiEnvelope[dict],
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_REGISTRY_OBJ, "PATCH")),
    ],
)
async def update_agent_status(
    agent_key: str,
    body: UpdateAgentStatusRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[dict]:
    agent = await service.set_agent_status(
        context=context, agent_key=agent_key, status=body.status
    )
    return ApiEnvelope(data={"agent_key": agent.agent_key, "status": agent.status})
