"""Team + agent registry endpoints — the minimal onboarding surface.

Admin endpoints use the browser session (log in, then Authorize in Swagger
with the CSRF token from /auth/me). The one bearer endpoint is
POST /agents/register — agents self-register with their team's token.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, status

from .....dto.base import ApiEnvelope
from .....dto.registry import (
    AgentModel,
    AgentRegistrationResponse,
    RegisterAgentRequest,
    RegisterTeamRequest,
    TeamModel,
    TeamTokenResponse,
    UpdateAgentStatusRequest,
)
from .....entity.agents import OdtTeamEntity, RegisteredAgentEntity, TeamTokenEntity
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.agents import AgentRegistryService, TeamTokenService
from .....utils.errors import UnauthorizedError, ValidationError
from ....dependencies import (
    provide_agent_registry_service,
    provide_team_token_service,
)

registry_router = APIRouter(prefix="/admin", tags=["Registry"])

_REGISTRY_OBJ = "/api/v1/admin"


def _to_team(team: OdtTeamEntity) -> TeamModel:
    return TeamModel(
        id=team.id,
        key=team.key,
        name=team.name,
        description=team.description,
        contact_email=team.contact_email,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def _to_agent(agent: RegisteredAgentEntity) -> AgentModel:
    return AgentModel(
        id=agent.id,
        agent_key=agent.agent_key,
        display_name=agent.display_name,
        description=agent.description,
        card_url=agent.card_url,
        version=agent.version,
        status=agent.status,
        permission=agent.permission,
        allowed_roles=list(agent.allowed_roles or []),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@registry_router.post(
    "/teams",
    response_model=ApiEnvelope[TeamModel],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_REGISTRY_OBJ, "POST"))],
)
async def register_team(
    body: RegisterTeamRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[TeamModel]:
    team = await service.register_team(
        context=context,
        key=body.key,
        name=body.name,
        description=body.description,
        contact_email=body.contact_email,
    )
    return ApiEnvelope(data=_to_team(team), message="Team registered.")


@registry_router.get(
    "/teams",
    response_model=ApiEnvelope[list[TeamModel]],
    dependencies=[Depends(require_permission(_REGISTRY_OBJ, "GET"))],
)
async def list_teams(
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[list[TeamModel]]:
    teams = await service.list_teams(context=context)
    return ApiEnvelope(data=[_to_team(t) for t in teams])


@registry_router.post(
    "/teams/{team_key}/tokens",
    response_model=ApiEnvelope[TeamTokenResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_REGISTRY_OBJ, "POST"))],
)
async def issue_team_token(
    team_key: str,
    context: SessionContext = Depends(get_current_context),
    tokens: TeamTokenService = Depends(provide_team_token_service),
) -> ApiEnvelope[TeamTokenResponse]:
    token = await tokens.issue(context=context, team_key=team_key)
    return ApiEnvelope(
        data=TeamTokenResponse(team_key=team_key.strip().lower(), token=token),
        message="Token issued. It is shown exactly once — store it securely.",
    )


@registry_router.post(
    "/agents",
    response_model=ApiEnvelope[AgentRegistrationResponse],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf), Depends(require_permission(_REGISTRY_OBJ, "POST"))],
)
async def register_agent(
    body: RegisterAgentRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[AgentRegistrationResponse]:
    agent, policies_seeded = await service.register_agent(
        context=context,
        team_key=body.team_key,
        agent_key=body.agent_key,
        display_name=body.display_name,
        description=body.description,
        card_url=body.card_url,
        version=body.version,
        permission=body.permission,
        allowed_roles=body.allowed_roles,
    )
    return ApiEnvelope(
        data=AgentRegistrationResponse(agent=_to_agent(agent), policies_seeded=policies_seeded),
        message="Agent registered.",
    )


@registry_router.get(
    "/agents",
    response_model=ApiEnvelope[list[AgentModel]],
    dependencies=[Depends(require_permission(_REGISTRY_OBJ, "GET"))],
)
async def list_agents(
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[list[AgentModel]]:
    agents = await service.list_agents(context=context)
    return ApiEnvelope(data=[_to_agent(a) for a in agents])


@registry_router.patch(
    "/agents/{agent_key}/status",
    response_model=ApiEnvelope[AgentModel],
    dependencies=[Depends(require_csrf), Depends(require_permission(_REGISTRY_OBJ, "PATCH"))],
)
async def update_agent_status(
    agent_key: str,
    body: UpdateAgentStatusRequest,
    context: SessionContext = Depends(get_current_context),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[AgentModel]:
    agent = await service.set_agent_status(
        context=context, agent_key=agent_key, status=body.status
    )
    return ApiEnvelope(data=_to_agent(agent), message="Status updated.")


# --- Agent self-registration (bearer team token, headless) -----------------

onboarding_router = APIRouter(prefix="/agents", tags=["Agent Onboarding"])


async def require_team_token(request: Request) -> TeamTokenEntity:
    authorization = request.headers.get("Authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not credential.strip():
        raise UnauthorizedError("A team registration token is required (Bearer).")
    from ....dependencies import container

    token = await container.team_token_service().validate(credential.strip())
    if token is None:
        raise UnauthorizedError("Invalid or revoked team registration token.")
    return token


def _service_context(token: TeamTokenEntity) -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id=f"registration-{token.team_key}",
        tenant_id=token.tenant_id,
        user_id=f"team:{token.team_key}",
        actor_id=f"team:{token.team_key}",
        email="",
        display_name=f"Team {token.team_key}",
        auth_provider="team_token",
        csrf_token="",
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=5),
        roles=("service",),
    )


@onboarding_router.post(
    "/register",
    response_model=ApiEnvelope[AgentRegistrationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def self_register(
    body: RegisterAgentRequest,
    token: TeamTokenEntity = Depends(require_team_token),
    service: AgentRegistryService = Depends(provide_agent_registry_service),
) -> ApiEnvelope[AgentRegistrationResponse]:
    """Idempotent self-registration — safe to call on every agent boot. The
    token pins the team; activation stays an explicit admin step."""
    if body.team_key.strip().lower() != token.team_key:
        raise ValidationError(
            "Registration token belongs to a different team.",
            details={"token_team": token.team_key, "requested_team": body.team_key},
        )
    agent, policies_seeded = await service.register_agent(
        context=_service_context(token),
        team_key=body.team_key,
        agent_key=body.agent_key,
        display_name=body.display_name,
        description=body.description,
        card_url=body.card_url,
        version=body.version,
        permission=body.permission,
        allowed_roles=body.allowed_roles,
    )
    return ApiEnvelope(
        data=AgentRegistrationResponse(agent=_to_agent(agent), policies_seeded=policies_seeded),
        message="Agent registered.",
    )
