from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, status

from .....dto.agents import (
    OnboardingRegistrationResponse,
    RegisterAgentRequest,
    RouteOverlapModel,
)
from .....dto.common import ApiEnvelope
from .....entity.agents import TeamTokenEntity
from .....security.session import SessionContext
from .....services.agents.registry_service import AgentRegistryService
from .....services.agents.route_index_service import RouteIndexService
from .....services.agents.team_token_service import get_team_token_service
from .....utils.common.logger import Logger
from .....utils.errors import UnauthorizedError, ValidationError
from ....dependencies import (
    provide_agent_registry_service,
    provide_route_index_service,
)

logger = Logger(__name__).get_logger()

agent_onboarding_v1_router = APIRouter(prefix="/agents", tags=["Agent Onboarding"])


async def require_team_token(request: Request) -> TeamTokenEntity:
    """Bearer team-token auth — headless, works from CI/CD and agent startup."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not credential.strip():
        raise UnauthorizedError("A team registration token is required (Bearer).")
    token = await get_team_token_service().validate(credential.strip())
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


@agent_onboarding_v1_router.post(
    "/register",
    response_model=ApiEnvelope[OnboardingRegistrationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def self_register(
    body: RegisterAgentRequest,
    token: TeamTokenEntity = Depends(require_team_token),
    registry: AgentRegistryService = Depends(provide_agent_registry_service),
    route_index: RouteIndexService = Depends(provide_route_index_service),
) -> ApiEnvelope[OnboardingRegistrationResponse]:
    """Agent self-registration: the agent announces itself on startup (or from
    a pipeline) with its team's token. Idempotent upsert — safe to call every
    boot. The token pins the team: registering under another team's key is
    rejected. Activation stays an explicit admin step."""
    if body.team_key.strip().lower() != token.team_key:
        raise ValidationError(
            "Registration token belongs to a different team.",
            details={"token_team": token.team_key, "requested_team": body.team_key},
        )

    context = _service_context(token)
    agent, team_key, policies_seeded = await registry.register_agent(
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

    overlaps = await route_index.rebuild_for_agent(
        tenant_id=context.tenant_id,
        agent_key=agent.agent_key,
        display_name=agent.display_name,
        description=agent.description,
        skills=[dict(s) for s in (agent.skills or [])],
    )
    routes = await route_index.list_for_agent(
        tenant_id=context.tenant_id, agent_key=agent.agent_key
    )

    message = "Agent registered."
    if overlaps:
        message = (
            "Agent registered with ROUTE OVERLAP warnings — sharpen your skill "
            "examples or confirm the overlap is intended before activation."
        )
    return ApiEnvelope(
        data=OnboardingRegistrationResponse(
            agent_key=agent.agent_key,
            team_key=team_key,
            status=agent.status,
            policies_seeded=policies_seeded,
            route_utterances=len(routes),
            route_overlaps=[RouteOverlapModel(**o) for o in overlaps],
        ),
        message=message,
    )
