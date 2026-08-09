from google.protobuf import json_format

from ace_agent_kit import ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config


class BenefitsAgent:
    """HR Benefits team logic — prompt-driven from the team's own manifest.

    Real implementation: retrieve from a benefits knowledge source
    (AceCapabilityClient.retrieve) and answer via the team's LLM deployment
    (AceCapabilityClient.llm_chat with the system prompt). The template
    answers with the versioned fallback prompt.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()

    async def handle(self, *, user_input: str, envelope: ContextEnvelope | None) -> str:
        question = user_input.strip() or "(empty question)"
        fallback = self._config.prompt_store.get("fallback")
        if fallback is not None:
            return fallback.format(
                display_name=self._config.display_name, question=question
            ).strip()
        return f"[{self._config.display_name}] Benefits question received: {question}"


class BenefitsAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = BenefitsAgent()

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
            new_text_message(result, context_id=context.context_id, task_id=context.task_id)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this template agent.")
