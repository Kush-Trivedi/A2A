"""One audit row per A2A dispatch — with the correlation id and task id,
every multi-agent chain is reconstructable after the fact."""

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.authz.policy_audit_log_entity import PolicyAuditLogEntity
from ...utils.common.logger import Logger
from .context_envelope import ContextEnvelope

logger = Logger(__name__).get_logger()


class A2ADispatchAuditor:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def record_dispatch(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        task_id: str,
        final_state: str,
    ) -> None:
        try:
            async with self._db.session() as session:
                session.add(
                    PolicyAuditLogEntity(
                        actor_id=envelope.actor_id or envelope.user_id,
                        tenant_id=envelope.tenant_id,
                        action="a2a_dispatch",
                        target_role=",".join(envelope.roles),
                        target_domain=envelope.correlation_id or envelope.session_id,
                        target_resource=f"agent:{agent_key}",
                        target_action=f"{final_state}:{task_id or '-'}",
                    )
                )
        except Exception:  # noqa: BLE001 — auditing must never break a turn
            logger.error(
                "A2A dispatch audit write failed",
                extra={"agent_key": agent_key},
                exc_info=True,
            )


_auditor: A2ADispatchAuditor | None = None


def get_dispatch_auditor() -> A2ADispatchAuditor:
    global _auditor
    if _auditor is None:
        _auditor = A2ADispatchAuditor()
    return _auditor
