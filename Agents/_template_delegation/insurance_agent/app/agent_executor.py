from google.protobuf import json_format

from ace_agent_kit import ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config


class InsuranceAgent:
    """Pay Ops team logic.

    The template verifies coverage with a canned response that echoes the
    received ContextEnvelope — proving to the caller (and the audit trail)
    that identity and delegation context crossed the team boundary intact.
    A real implementation queries Pay Ops' Databricks resources (agent.yaml
    `data`) for eligibility and claims.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()

    async def handle(
        self, *, user_input: str, envelope: ContextEnvelope | None
    ) -> str:
        request = user_input.strip() or "(empty request)"
        if envelope is None:
            return (
                f"[{self._config.display_name}] Coverage check received without "
                f"caller context: {request}. Unable to verify without an ACE "
                "context envelope."
            )

        delegated = (
            f" (delegated from '{envelope.delegated_from}': {envelope.delegation_reason})"
            if envelope.delegated_from
            else ""
        )
        return (
            f"[{self._config.display_name}] Coverage verified for actor "
            f"{envelope.actor_id} in tenant {envelope.tenant_id}{delegated}. "
            f"Request: {request} Eligibility: confirmed (template stub)."
        )


class InsuranceAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = InsuranceAgent()

    @staticmethod
    def _envelope_from(context: RequestContext) -> ContextEnvelope | None:
        if context.message is None:
            return None
        metadata = json_format.MessageToDict(context.message.metadata)
        return ContextEnvelope.from_metadata(metadata)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        result = await self._agent.handle(
            user_input=context.get_user_input(),
            envelope=self._envelope_from(context),
        )
        await event_queue.enqueue_event(
            new_text_message(
                result,
                context_id=context.context_id,
                task_id=context.task_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this template agent.")
