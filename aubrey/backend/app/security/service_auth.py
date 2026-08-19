"""Service-plane security for /api/v1/capability — how AGENTS call aubrey.

Two layers, both mandatory:

1. WHO is calling: the bearer token is a team registration token; the
   agent_key in the request must belong to that token's team. A team's
   token can never act as another team's agent.
2. ON WHOSE BEHALF: the request body carries a context envelope with the
   end user's identity and roles. Roles are RE-ENFORCED here with Casbin
   against the target agent's permission — an agent cannot escalate a
   user's access by forwarding inflated roles it never received.

M10-S3 adds a third layer: when envelope signing is enabled
(security.envelope_signing.key), the envelope must carry the platform's
HMAC (sig + issued_at, forwarded verbatim from dispatch) and be fresh —
agents can no longer mint identities. With no key configured, verification
passes everything through (dev mode)."""

from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlmodel import select

from ..database.rdbms.pg_session import get_postgres_connector
from ..dto.capability import ContextEnvelopeModel
from ..entity.agents import AgentStatus, OdtTeamEntity, RegisteredAgentEntity, TeamTokenEntity
from ..security.authorization.enforcer import get_casbin_enforcer
from ..security.session import SessionContext
from ..services.a2a.envelope_signer import get_envelope_signer
from ..utils.common.logger import Logger
from ..utils.errors import ForbiddenError, NotFoundError, UnauthorizedError

logger = Logger(__name__).get_logger()

_SERVICE_CONTEXT_TTL = timedelta(minutes=5)


async def require_service_token(request: Request) -> TeamTokenEntity:
    """FastAPI dependency: validates the team-token bearer on the service plane."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not credential.strip():
        raise UnauthorizedError("A team service token is required (Bearer).")
    from ..api.dependencies import container

    token = await container.team_token_service().validate(credential.strip())
    if token is None:
        raise UnauthorizedError("Invalid or revoked team service token.")
    return token


async def resolve_owned_agent(
    *, token: TeamTokenEntity, agent_key: str
) -> RegisteredAgentEntity:
    """The agent the caller claims to be must exist, be ACTIVE, and belong
    to the token's team."""
    normalized = agent_key.strip().lower()
    async with get_postgres_connector().session() as session:
        team = (
            await session.exec(
                select(OdtTeamEntity).where(
                    OdtTeamEntity.tenant_id == token.tenant_id,
                    OdtTeamEntity.key == token.team_key,
                )
            )
        ).first()
        agent = (
            await session.exec(
                select(RegisteredAgentEntity).where(
                    RegisteredAgentEntity.tenant_id == token.tenant_id,
                    RegisteredAgentEntity.agent_key == normalized,
                )
            )
        ).first()
    if team is None or agent is None or agent.team_id != team.id:
        raise NotFoundError(
            f"Agent '{normalized}' is not registered under team '{token.team_key}'.",
            details={"agent_key": normalized, "team_key": token.team_key},
        )
    if agent.status != AgentStatus.ACTIVE:
        raise ForbiddenError(
            f"Agent '{normalized}' is not active — an admin must activate it first."
        )
    return agent


def context_from_envelope(
    envelope: ContextEnvelopeModel, *, token: TeamTokenEntity
) -> SessionContext:
    """Synthetic, short-lived SessionContext for the forwarded end user.
    The tenant always comes from the TOKEN, never from the envelope."""
    now = datetime.now(timezone.utc)
    return SessionContext(
        session_id=envelope.session_id or f"service-{token.team_key}",
        tenant_id=token.tenant_id,
        user_id=envelope.user_id,
        actor_id=envelope.actor_id or envelope.user_id,
        email="",
        display_name=envelope.user_id,
        auth_provider="service",
        csrf_token="",
        created_at=now,
        last_seen_at=now,
        expires_at=now + _SERVICE_CONTEXT_TTL,
        roles=tuple(envelope.roles),
    )


def verify_envelope_signature(
    envelope: ContextEnvelopeModel, *, tenant_id: str
) -> None:
    """The envelope must be one the platform actually signed at dispatch —
    same identity fields, bound to the TOKEN's tenant, within the freshness
    window. No-op while signing is disabled (placeholder key)."""
    get_envelope_signer().verify(
        {
            "user_id": envelope.user_id,
            "actor_id": envelope.actor_id,
            "roles": list(envelope.roles),
            "session_id": envelope.session_id or "",
            "purpose": envelope.purpose,
            "delegated_from": list(envelope.delegated_from),
            "sig": envelope.sig,
            "issued_at": envelope.issued_at,
        },
        tenant_id=tenant_id,
    )


async def enforce_agent_access(
    *, envelope: ContextEnvelopeModel, agent: RegisteredAgentEntity, tenant_id: str
) -> None:
    """The end user's roles must permit this agent (same rule the chat
    router applies before dispatching). Agents with no declared permission
    are open; a user with no roles is always denied. When envelope signing
    is enabled, an unsigned/tampered/stale envelope is rejected FIRST —
    roles are only trusted once the platform's signature over them holds."""
    verify_envelope_signature(envelope, tenant_id=tenant_id)
    if not agent.permission:
        return
    if not envelope.roles:
        raise ForbiddenError("The forwarded user has no roles — access denied.")
    allowed = await get_casbin_enforcer().enforce_any_role(
        envelope.roles, tenant_id, f"agent:{agent.agent_key}", agent.permission
    )
    if not allowed:
        raise ForbiddenError(
            f"The user's roles do not permit agent '{agent.agent_key}'.",
            details={"agent_key": agent.agent_key, "roles": list(envelope.roles)},
        )
