from google.protobuf import json_format

from ace_agent_kit import AgentDelegator, ContextEnvelope, DelegationResult

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_INSURANCE_CAPABILITY = "insurance"


class SchedulingAgent:
    """The team's actual agent logic.

    Books the appointment (template stub), then delegates insurance
    verification to the Pay Ops agent when that delegation is configured —
    forwarding the received ContextEnvelope (stamped with delegated_from) and
    referencing the current task so the chain stays auditable.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        delegator: AgentDelegator | None = None,
    ) -> None:
        self._config = config or get_agent_config()
        self._delegator = delegator or AgentDelegator()

    async def handle(
        self,
        *,
        user_input: str,
        envelope: ContextEnvelope | None,
        context_id: str,
        task_id: str,
    ) -> str:
        question = user_input.strip() or "(empty message)"
        ack_prompt = self._config.prompt_store.get("booking_ack")
        if ack_prompt is not None:
            booking_ack = ack_prompt.format(
                display_name=self._config.display_name, question=question
            ).strip()
        else:
            booking_ack = (
                f"[{self._config.display_name}] Received scheduling request: {question}."
            )

        delegation = await self._verify_insurance(
            question=question, envelope=envelope, context_id=context_id, task_id=task_id
        )
        if delegation is None:
            return booking_ack
        return f"{booking_ack}\n\n{delegation.text}"

    async def _verify_insurance(
        self,
        *,
        question: str,
        envelope: ContextEnvelope | None,
        context_id: str,
        task_id: str,
    ) -> DelegationResult | None:
        target = self._config.delegations.get(_INSURANCE_CAPABILITY)
        if target is None or envelope is None:
            return None

        reason_prompt = self._config.prompt_store.get("delegation_reason")
        delegated_envelope = envelope.with_delegation(
            delegated_from=self._config.agent_key,
            reason=(reason_prompt.content.strip() if reason_prompt else "Delegated follow-up."),
        )
        return await self._delegator.delegate(
            target=target,
            text=f"Verify insurance coverage for this appointment request: {question}",
            envelope=delegated_envelope,
            context_id=context_id,
            reference_task_ids=(task_id,) if task_id else (),
        )


class SchedulingAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = SchedulingAgent()

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
            context_id=context.context_id or "",
            task_id=context.task_id or "",
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
