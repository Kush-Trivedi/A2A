from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_ACCESS_KEYWORDS = ("access", "who can", "what can i", "permission", "help me with")


class GeneralAgent:
    """ACE platform general assistant.

    Access questions are answered from the live, role-scoped catalog (via the
    ACE capability API — Casbin does the scoping). Everything else gets a safe
    template answer; teams plug their LLM of choice here later.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        capabilities: AceCapabilityClient | None = None,
    ) -> None:
        self._config = config or get_agent_config()
        self._capabilities = capabilities or AceCapabilityClient(
            base_url=self._config.ace_base_url
        )

    async def handle(
        self, *, user_input: str, envelope: ContextEnvelope | None
    ) -> str:
        question = user_input.strip().lower()
        if envelope is not None and any(k in question for k in _ACCESS_KEYWORDS):
            return await self._access_overview(envelope)
        return (
            f"[{self._config.display_name}] I can answer general questions and "
            "tell you which assistants your role can use — try asking "
            "'what can I access?'. For clinical, scheduling, or payer topics "
            "I will point you to the right specialist assistant."
        )

    async def _access_overview(self, envelope: ContextEnvelope) -> str:
        agents = await self._capabilities.accessible_agents(envelope=envelope)
        if not agents:
            return (
                "Your current roles have no assistants assigned yet. "
                "Please contact your administrator to request access."
            )
        lines = [
            f"Based on your roles ({', '.join(envelope.roles) or 'none'}), "
            "you can use these assistants:"
        ]
        for agent in agents:
            owner = f" (team: {agent.team_key})" if agent.team_key else ""
            lines.append(f"- **{agent.display_name}**{owner} — {agent.description}")
        lines.append(
            "If you need something not listed, the owning team can grant access."
        )
        return "\n".join(lines)


class GeneralAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = GeneralAgent()

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
                result, context_id=context.context_id, task_id=context.task_id
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this template agent.")
