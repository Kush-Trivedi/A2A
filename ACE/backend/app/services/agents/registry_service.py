import uuid
from sqlmodel import select
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import (
    AgentStatus,
    AgentVersionEntity,
    AgentVersionStatus,
    OdtTeamEntity,
    RegisteredAgentEntity,
)
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError
from ..a2a.agent_card_service import AgentCardService, get_agent_card_service

logger = Logger(__name__).get_logger()

_RETRIEVAL_MODES = {"dense", "sparse", "hybrid"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentRegistryService:
    def __init__(
        self,
        enforcer: CasbinEnforcer | None = None,
        card_service: AgentCardService | None = None,
    ) -> None:
        self._connector = get_postgres_connector()
        self._enforcer = enforcer or get_casbin_enforcer()
        self._card_service = card_service or get_agent_card_service()

    async def register_team(
        self, *, context: SessionContext, key: str, name: str,
        description: str = "", contact_email: str | None = None,
    ) -> OdtTeamEntity:
        now = _now()
        stmt = (
            pg_insert(OdtTeamEntity)
            .values(
                id=uuid.uuid4().hex,
                tenant_id=context.tenant_id,
                key=key,
                name=name,
                description=description,
                contact_email=contact_email,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_odt_teams_tenant_key",
                set_={
                    "name": name,
                    "description": description,
                    "contact_email": contact_email,
                    "updated_at": now,
                },
            )
        )
        try:
            async with self._connector.session() as session:
                await session.exec(stmt)
                team = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == key,
                        )
                    )
                ).one()
                logger.info(
                    "ODT team registered",
                    extra={"tenant_id": context.tenant_id, "team_key": key},
                )
                return team
        except Exception as exc:
            logger.error("register_team failed", extra={"error": str(exc)}, exc_info=True)
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
        aliases: list[str] | None = None,
        knowledge_sources: list[str] | None = None,
        retrieval_mode: str | None = None,
        team_config: dict | None = None,
        prompts: dict | None = None,
    ) -> tuple[RegisteredAgentEntity, str, int]:
        if retrieval_mode and retrieval_mode not in _RETRIEVAL_MODES:
            raise ValidationError(
                f"Unknown retrieval mode '{retrieval_mode}'. "
                f"Valid modes: {', '.join(sorted(_RETRIEVAL_MODES))}."
            )

        roles = list(dict.fromkeys(r.strip() for r in (allowed_roles or []) if r.strip()))
        now = _now()

        skills: list[dict] = []
        card_snapshot: dict = {}
        if card_url:
            validated = await self._card_service.fetch_and_validate(card_url)
            skills = [dict(skill) for skill in validated.skills]
            card_snapshot = validated.snapshot
            logger.info(
                "Agent card validated",
                extra={
                    "agent_key": agent_key,
                    "card_name": validated.name,
                    "skills": list(validated.skill_ids),
                },
            )

        try:
            async with self._connector.session() as session:
                team = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == team_key,
                        )
                    )
                ).first()
                if team is None:
                    raise NotFoundError(
                        f"Team '{team_key}' is not registered.",
                        details={"team_key": team_key},
                    )

                stmt = (
                    pg_insert(RegisteredAgentEntity)
                    .values(
                        id=uuid.uuid4().hex,
                        tenant_id=context.tenant_id,
                        team_id=team.id,
                        agent_key=agent_key,
                        display_name=display_name,
                        description=description,
                        card_url=card_url,
                        version=version,
                        status=AgentStatus.REGISTERED,
                        permission=permission,
                        aliases=aliases or [],
                        knowledge_sources=knowledge_sources or [],
                        allowed_roles=roles,
                        retrieval_mode=retrieval_mode,
                        team_config=team_config or {},
                        skills=skills,
                        card_snapshot=card_snapshot,
                        prompts=prompts or {},
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        constraint="uq_registered_agents_tenant_key",
                        set_={
                            "team_id": team.id,
                            "display_name": display_name,
                            "description": description,
                            "card_url": card_url,
                            "version": version,
                            "permission": permission,
                            "aliases": aliases or [],
                            "knowledge_sources": knowledge_sources or [],
                            "allowed_roles": roles,
                            "retrieval_mode": retrieval_mode,
                            "team_config": team_config or {},
                            "skills": skills,
                            "card_snapshot": card_snapshot,
                            "prompts": prompts or {},
                            "updated_at": now,
                        },
                    )
                )
                await session.exec(stmt)
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key,
                        )
                    )
                ).one()
                await self._record_version(session, agent)

            policies_seeded = await self._seed_policies(
                tenant_id=context.tenant_id,
                agent_key=agent_key,
                permission=permission,
                roles=roles,
            )
            logger.info(
                "Agent registered",
                extra={
                    "tenant_id": context.tenant_id,
                    "team_key": team_key,
                    "agent_key": agent_key,
                    "policies_seeded": policies_seeded,
                },
            )
            return agent, team_key, policies_seeded
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            logger.error("register_agent failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

    @staticmethod
    async def _record_version(session, agent: RegisteredAgentEntity) -> None:
        now = _now()
        demote = (
            pg_insert(AgentVersionEntity)
            .values(
                id=uuid.uuid4().hex,
                tenant_id=agent.tenant_id,
                agent_key=agent.agent_key,
                version=agent.version,
                status=AgentVersionStatus.CURRENT,
                display_name=agent.display_name,
                description=agent.description,
                card_url=agent.card_url,
                permission=agent.permission,
                retrieval_mode=agent.retrieval_mode,
                aliases=agent.aliases or [],
                knowledge_sources=agent.knowledge_sources or [],
                allowed_roles=agent.allowed_roles or [],
                team_config=agent.team_config or {},
                skills=agent.skills or [],
                card_snapshot=agent.card_snapshot or {},
                prompts=agent.prompts or {},
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_agent_versions_tenant_key_ver",
                set_={
                    "status": AgentVersionStatus.CURRENT,
                    "display_name": agent.display_name,
                    "description": agent.description,
                    "card_url": agent.card_url,
                    "permission": agent.permission,
                    "retrieval_mode": agent.retrieval_mode,
                    "aliases": agent.aliases or [],
                    "knowledge_sources": agent.knowledge_sources or [],
                    "allowed_roles": agent.allowed_roles or [],
                    "team_config": agent.team_config or {},
                    "skills": agent.skills or [],
                    "card_snapshot": agent.card_snapshot or {},
                    "prompts": agent.prompts or {},
                    "updated_at": now,
                },
            )
        )
        await session.exec(demote)
        others = (
            await session.exec(
                select(AgentVersionEntity).where(
                    AgentVersionEntity.tenant_id == agent.tenant_id,
                    AgentVersionEntity.agent_key == agent.agent_key,
                    AgentVersionEntity.version != agent.version,
                    AgentVersionEntity.status == AgentVersionStatus.CURRENT,
                )
            )
        ).all()
        for row in others:
            row.status = AgentVersionStatus.SUPERSEDED
            row.updated_at = now
            session.add(row)

    async def list_agent_versions(
        self, *, tenant_id: str, agent_key: str
    ) -> list[AgentVersionEntity]:
        try:
            async with self._connector.session() as session:
                rows = await session.exec(
                    select(AgentVersionEntity)
                    .where(
                        AgentVersionEntity.tenant_id == tenant_id,
                        AgentVersionEntity.agent_key == agent_key,
                    )
                    .order_by(AgentVersionEntity.created_at.desc())  # type: ignore[union-attr]
                )
                return list(rows.all())
        except Exception as exc:
            logger.error("list_agent_versions failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

    async def activate_version(
        self, *, context: SessionContext, agent_key: str, version: str
    ) -> RegisteredAgentEntity:
        try:
            async with self._connector.session() as session:
                snapshot = (
                    await session.exec(
                        select(AgentVersionEntity).where(
                            AgentVersionEntity.tenant_id == context.tenant_id,
                            AgentVersionEntity.agent_key == agent_key,
                            AgentVersionEntity.version == version,
                        )
                    )
                ).first()
                if snapshot is None:
                    raise NotFoundError(
                        f"Version '{version}' of agent '{agent_key}' is not recorded.",
                        details={"agent_key": agent_key, "version": version},
                    )
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key,
                        )
                    )
                ).one()
                agent.version = snapshot.version
                agent.display_name = snapshot.display_name
                agent.description = snapshot.description
                agent.card_url = snapshot.card_url
                agent.permission = snapshot.permission
                agent.retrieval_mode = snapshot.retrieval_mode
                agent.aliases = list(snapshot.aliases or [])
                agent.knowledge_sources = list(snapshot.knowledge_sources or [])
                agent.allowed_roles = list(snapshot.allowed_roles or [])
                agent.team_config = dict(snapshot.team_config or {})
                agent.skills = list(snapshot.skills or [])
                agent.card_snapshot = dict(snapshot.card_snapshot or {})
                agent.prompts = dict(snapshot.prompts or {})
                agent.updated_at = _now()
                session.add(agent)
                await self._record_version(session, agent)

            await self._seed_policies(
                tenant_id=context.tenant_id,
                agent_key=agent_key,
                permission=agent.permission,
                roles=list(agent.allowed_roles or []),
            )
            logger.info(
                "Agent version activated",
                extra={"agent_key": agent_key, "version": version},
            )
            return agent
        except NotFoundError:
            raise
        except Exception as exc:
            logger.error("activate_version failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

    async def _seed_policies(
        self, *, tenant_id: str, agent_key: str, permission: str, roles: list[str]
    ) -> int:
        if not self._enforcer.enabled or not roles:
            return 0
        seeded = 0
        for role in roles:
            added = await self._enforcer.add_policy(
                role, tenant_id, f"agent:{agent_key}", permission
            )
            seeded += int(bool(added))
        return seeded

    async def list_teams(self, *, context: SessionContext) -> list[OdtTeamEntity]:
        try:
            async with self._connector.session() as session:
                rows = await session.exec(
                    select(OdtTeamEntity)
                    .where(OdtTeamEntity.tenant_id == context.tenant_id)
                    .order_by(OdtTeamEntity.key)
                )
                return list(rows.all())
        except Exception as exc:
            logger.error("list_teams failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

    async def list_agents(
        self, *, context: SessionContext
    ) -> list[tuple[RegisteredAgentEntity, str]]:
        try:
            async with self._connector.session() as session:
                rows = await session.exec(
                    select(RegisteredAgentEntity, OdtTeamEntity.key)
                    .join(OdtTeamEntity, OdtTeamEntity.id == RegisteredAgentEntity.team_id)
                    .where(RegisteredAgentEntity.tenant_id == context.tenant_id)
                    .order_by(RegisteredAgentEntity.agent_key)
                )
                return [(agent, team_key) for agent, team_key in rows.all()]
        except Exception as exc:
            logger.error("list_agents failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc

    async def find_active_agent(
        self, *, tenant_id: str, key: str
    ) -> RegisteredAgentEntity | None:
        normalized = (key or "").strip().lower()
        if not normalized:
            return None
        try:
            async with self._connector.session() as session:
                agents = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == tenant_id,
                            RegisteredAgentEntity.status == AgentStatus.ACTIVE,
                        )
                    )
                ).all()
        except Exception as exc:
            logger.error(
                "find_active_agent failed", extra={"error": str(exc)}, exc_info=True
            )
            raise DatabaseError(cause=exc) from exc

        for agent in agents:
            if agent.agent_key.lower() == normalized:
                return agent
            if any(str(alias).strip().lower() == normalized for alias in agent.aliases or []):
                return agent
        return None

    async def get_agent_with_team(
        self, *, tenant_id: str, agent_key: str
    ) -> tuple[RegisteredAgentEntity, OdtTeamEntity] | None:
        try:
            async with self._connector.session() as session:
                row = (
                    await session.exec(
                        select(RegisteredAgentEntity, OdtTeamEntity)
                        .join(OdtTeamEntity, OdtTeamEntity.id == RegisteredAgentEntity.team_id)
                        .where(
                            RegisteredAgentEntity.tenant_id == tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key,
                        )
                    )
                ).first()
                return (row[0], row[1]) if row else None
        except Exception as exc:
            logger.error(
                "get_agent_with_team failed", extra={"error": str(exc)}, exc_info=True
            )
            raise DatabaseError(cause=exc) from exc

    async def list_active_agents_with_teams(
        self, *, tenant_id: str
    ) -> list[tuple[RegisteredAgentEntity, OdtTeamEntity]]:
        try:
            async with self._connector.session() as session:
                rows = await session.exec(
                    select(RegisteredAgentEntity, OdtTeamEntity)
                    .join(OdtTeamEntity, OdtTeamEntity.id == RegisteredAgentEntity.team_id)
                    .where(
                        RegisteredAgentEntity.tenant_id == tenant_id,
                        RegisteredAgentEntity.status == AgentStatus.ACTIVE,
                    )
                    .order_by(RegisteredAgentEntity.agent_key)
                )
                return [(agent, team) for agent, team in rows.all()]
        except Exception as exc:
            logger.error(
                "list_active_agents_with_teams failed",
                extra={"error": str(exc)},
                exc_info=True,
            )
            raise DatabaseError(cause=exc) from exc

    async def list_active_agent_cards(self) -> list[tuple[str, str]]:
        try:
            async with self._connector.session() as session:
                rows = await session.exec(
                    select(
                        RegisteredAgentEntity.agent_key,
                        RegisteredAgentEntity.card_url,
                    )
                    .where(
                        RegisteredAgentEntity.status == AgentStatus.ACTIVE,
                        RegisteredAgentEntity.card_url.is_not(None),
                    )
                    .order_by(RegisteredAgentEntity.agent_key)
                )
                return [(agent_key, card_url) for agent_key, card_url in rows.all() if card_url]
        except Exception as exc:
            logger.error(
                "list_active_agent_cards failed", extra={"error": str(exc)}, exc_info=True
            )
            raise DatabaseError(cause=exc) from exc

    async def set_agent_status(
        self, *, context: SessionContext, agent_key: str, status: str
    ) -> RegisteredAgentEntity:
        if status not in AgentStatus.ALL:
            raise ValidationError(
                f"Unknown status '{status}'. Valid statuses: {', '.join(AgentStatus.ALL)}."
            )
        try:
            async with self._connector.session() as session:
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key,
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
            logger.error("set_agent_status failed", extra={"error": str(exc)}, exc_info=True)
            raise DatabaseError(cause=exc) from exc


_registry_service: AgentRegistryService | None = None


def get_agent_registry_service() -> AgentRegistryService:
    global _registry_service
    if _registry_service is None:
        _registry_service = AgentRegistryService()
    return _registry_service
