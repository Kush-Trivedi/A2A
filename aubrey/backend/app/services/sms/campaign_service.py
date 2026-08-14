"""SMS campaigns — the unit teams register. A campaign binds a name to
the registered agent that writes its messages, plus the directionality
flag: OUTREACH (we send; replies are stored, never answered) or
BIDIRECTIONAL (replies continue the conversation with the agent)."""

import uuid

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import AgentStatus, RegisteredAgentEntity
from ...entity.sms import CampaignMode, SmsCampaignEntity
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()


class SmsCampaignService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def register(
        self,
        *,
        context: SessionContext,
        key: str,
        agent_key: str,
        mode: str,
        description: str = "",
    ) -> SmsCampaignEntity:
        cleaned_key = key.strip().lower()
        cleaned_agent = agent_key.strip().lower()
        cleaned_mode = mode.strip().lower()
        if not cleaned_key:
            raise ValidationError("Campaign key must not be empty.")
        if cleaned_mode not in CampaignMode.ALL:
            raise ValidationError(
                f"Unknown campaign mode '{cleaned_mode}'. Use one of: "
                f"{', '.join(CampaignMode.ALL)}."
            )
        try:
            async with self._db.session() as session:
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == context.tenant_id,
                            RegisteredAgentEntity.agent_key == cleaned_agent,
                        )
                    )
                ).first()
                if agent is None:
                    raise NotFoundError(
                        f"Agent '{cleaned_agent}' is not registered.",
                        details={"agent_key": cleaned_agent},
                    )
                existing = (
                    await session.exec(
                        select(SmsCampaignEntity).where(
                            SmsCampaignEntity.tenant_id == context.tenant_id,
                            SmsCampaignEntity.key == cleaned_key,
                        )
                    )
                ).first()
                if existing is not None:
                    existing.agent_key = cleaned_agent
                    existing.mode = cleaned_mode
                    existing.description = description
                    session.add(existing)
                    return existing
                campaign = SmsCampaignEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=context.tenant_id,
                    key=cleaned_key,
                    agent_key=cleaned_agent,
                    mode=cleaned_mode,
                    description=description,
                )
                session.add(campaign)
                logger.info(
                    "SMS campaign registered",
                    extra={"key": cleaned_key, "agent_key": cleaned_agent, "mode": cleaned_mode},
                )
                return campaign
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list(self, *, tenant_id: str) -> list[SmsCampaignEntity]:
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(SmsCampaignEntity)
                        .where(SmsCampaignEntity.tenant_id == tenant_id)
                        .order_by(SmsCampaignEntity.key)  # type: ignore[arg-type]
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def get(self, *, tenant_id: str, key: str) -> SmsCampaignEntity:
        cleaned = key.strip().lower()
        try:
            async with self._db.session() as session:
                campaign = (
                    await session.exec(
                        select(SmsCampaignEntity).where(
                            SmsCampaignEntity.tenant_id == tenant_id,
                            SmsCampaignEntity.key == cleaned,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if campaign is None:
            raise NotFoundError(
                f"SMS campaign '{cleaned}' is not registered.", details={"key": cleaned}
            )
        return campaign

    async def active_agent(
        self, *, tenant_id: str, agent_key: str
    ) -> RegisteredAgentEntity:
        """The campaign's voice must exist, be ACTIVE and reachable."""
        try:
            async with self._db.session() as session:
                agent = (
                    await session.exec(
                        select(RegisteredAgentEntity).where(
                            RegisteredAgentEntity.tenant_id == tenant_id,
                            RegisteredAgentEntity.agent_key == agent_key,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if agent is None or agent.status != AgentStatus.ACTIVE or not agent.card_url:
            raise NotFoundError(
                f"Agent '{agent_key}' is not active/reachable — activate it "
                "before running its campaign.",
                details={"agent_key": agent_key},
            )
        return agent


_service: SmsCampaignService | None = None


def get_sms_campaign_service() -> SmsCampaignService:
    global _service
    if _service is None:
        _service = SmsCampaignService()
    return _service
