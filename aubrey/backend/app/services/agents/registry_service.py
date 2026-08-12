"""Minimal agent registry: teams, agents, Casbin policy seeding, status.

Registration stores what the team declares — no card fetching, no version
snapshots, no routing index yet. Those return only when they earn their
place. Every failure raises a typed error; nothing degrades silently.
"""

import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import AgentStatus, OdtTeamEntity, RegisteredAgentEntity
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRegistryService:
    def __init__(self, enforcer: CasbinEnforcer | None = None) -> None:
        self._connector = get_postgres_connector()
        self._enforcer = enforcer or get_casbin_enforcer()

    async def register_team(
        self,
        *,
        context: SessionContext,
        key: str,
        name: str,
        description: str = "",
        contact_email: str | None = None,
    ) -> OdtTeamEntity:
        team_key = key.strip().lower()
        if not team_key:
            raise ValidationError("Team key must not be empty.")
        try:
            async with self._connector.session() as session:
                existing = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == team_key,
                        )
                    )
                ).first()
                if existing is not None:
                    existing.name = name
                    existing.description = description
                    existing.contact_email = contact_email
                    session.add(existing)
                    return existing
                team = OdtTeamEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=context.tenant_id,
                    key=team_key,
                    name=name,
                    description=description,
                    contact_email=contact_email,
                )
                session.add(team)
                return team
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_teams(self, *, context: SessionContext) -> list[OdtTeamEntity]:
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(OdtTeamEntity)
                        .where(OdtTeamEntity.tenant_id == context.tenant_id)
                        .order_by(OdtTeamEntity.key)
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def register_agent(
        self,
        *,
        context: SessionContext,
        team_key: str,
        agent_key: str,
        display_name: str,
        description: str = "",
        card_url: str | None = None,
        version: str = "0.1.0",
        permission: str = "chat",
        allowed_roles: list[str] | None = None,
        skills: list[dict] | None = None,
    ) -> tuple[RegisteredAgentEntity, int, int, list[dict]]:
        normalized_team = team_key.strip().lower()
        normalized_agent = agent_key.strip().lower()
        if not normalized_agent:
            raise ValidationError("Agent key must not be empty.")
        roles = list(dict.fromkeys(r.strip() for r in (allowed_roles or []) if r.strip()))
        declared_skills = [dict(s) for s in (skills or [])]

        try:
            async with self._connector.session() as session:
                team = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == normalized_team,
                        )
                    )
                ).first()
                if team is None:
                    raise NotFoundError(
                        f"Team '{normalized_team}' is not registered.",
                        details={"team_key": normalized_team},
                    )

                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == normalized_agent,
                        )
                    )
                ).first()
                if agent is not None:
                    if agent.team_id != team.id:
                        raise ValidationError(
                            "Agent key is owned by another team.",
                            details={"agent_key": normalized_agent},
                        )
                    agent.display_name = display_name
                    agent.description = description
                    # A changed card URL on an ACTIVE agent requires re-approval.
                    if agent.card_url != card_url and agent.status == AgentStatus.ACTIVE:
                        agent.status = AgentStatus.REGISTERED
                        logger.warning(
                            "card_url changed on an active agent — status reset "
                            "to 'registered'; an admin must re-activate",
                            extra={"agent_key": normalized_agent},
                        )
                    agent.card_url = card_url
                    agent.version = version
                    agent.permission = permission
                    agent.allowed_roles = roles
                    agent.skills = declared_skills
                    agent.updated_at = _now()
                    session.add(agent)
                else:
                    agent = RegisteredAgentEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=context.tenant_id,
                        team_id=team.id,
                        agent_key=normalized_agent,
                        display_name=display_name,
                        description=description,
                        card_url=card_url,
                        version=version,
                        status=AgentStatus.REGISTERED,
                        permission=permission,
                        allowed_roles=roles,
                        skills=declared_skills,
                    )
                    session.add(agent)
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        policies_seeded = await self._seed_policies(
            tenant_id=context.tenant_id,
            agent_key=normalized_agent,
            permission=permission,
            roles=roles,
        )
        # The declared description/skills ARE the routing surface — rebuild
        # this agent's utterance index on every (re-)registration.
        from .route_index_service import get_route_index_service

        route_utterances, route_overlaps = await get_route_index_service().rebuild_for_agent(
            tenant_id=context.tenant_id, agent=agent
        )
        logger.info(
            "Agent registered",
            extra={"agent_key": normalized_agent, "team_key": normalized_team},
        )
        return agent, policies_seeded, route_utterances, route_overlaps

    async def _seed_policies(
        self, *, tenant_id: str, agent_key: str, permission: str, roles: list[str]
    ) -> int:
        if not roles:
            return 0
        seeded = 0
        for role in roles:
            added = await self._enforcer.add_policy(
                role, tenant_id, f"agent:{agent_key}", permission
            )
            seeded += int(bool(added))
        return seeded

    async def list_agents(self, *, context: SessionContext) -> list[RegisteredAgentEntity]:
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(RegisteredAgentEntity)
                        .where(RegisteredAgentEntity.tenant_id == context.tenant_id)
                        .order_by(RegisteredAgentEntity.agent_key)
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def set_agent_status(
        self, *, context: SessionContext, agent_key: str, status: str
    ) -> RegisteredAgentEntity:
        if status not in (AgentStatus.REGISTERED, AgentStatus.ACTIVE, AgentStatus.DISABLED):
            raise ValidationError(f"Unknown agent status '{status}'.")
        try:
            async with self._connector.session() as session:
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key.strip().lower(),
                        )
                    )
                ).first()
                if agent is None:
                    raise NotFoundError(
                        f"Agent '{agent_key}' is not registered.",
                        details={"agent_key": agent_key},
                    )
                agent.status = status
                agent.updated_at = _now()
                session.add(agent)
                return agent
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: AgentRegistryService | None = None


def get_agent_registry_service() -> AgentRegistryService:
    global _service
    if _service is None:
        _service = AgentRegistryService()
    return _service
