import uuid

from ace_agent_kit import ContextEnvelope

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.authz.policy_audit_log_entity import PolicyAuditLogEntity
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_ACTION_DISPATCH = "a2a_dispatch"


class A2ADispatchAuditor:
    """Writes one policy_audit_log row per A2A dispatch.

    Together with referenceTaskIds inside the protocol messages this makes
    every cross-agent chain reconstructable: who asked, which agent was
    called, under which correlation id, and how the task ended.
    """

    def __init__(self) -> None:
        self._connector = get_postgres_connector()

    async def record_dispatch(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        task_id: str,
        final_state: str,
    ) -> None:
        entry = PolicyAuditLogEntity(
            id=None,
            actor_id=envelope.actor_id or "unknown",
            tenant_id=envelope.tenant_id or "unknown",
            action=_ACTION_DISPATCH,
            target_role=envelope.delegated_from or "user",
            target_domain=envelope.correlation_id or envelope.chat_session_id or "-",
            target_resource=f"agent:{agent_key}",
            target_action=f"{final_state}:{task_id}" if task_id else final_state,
        )
        try:
            async with self._connector.session() as session:
                session.add(entry)
        except Exception:  # noqa: BLE001 — auditing must never break chat
            logger.error(
                "A2A dispatch audit write failed",
                extra={"agent_key": agent_key, "correlation_id": envelope.correlation_id},
                exc_info=True,
            )

    @staticmethod
    def new_correlation_id() -> str:
        return uuid.uuid4().hex


_auditor: A2ADispatchAuditor | None = None


def get_dispatch_auditor() -> A2ADispatchAuditor:
    global _auditor
    if _auditor is None:
        _auditor = A2ADispatchAuditor()
    return _auditor
