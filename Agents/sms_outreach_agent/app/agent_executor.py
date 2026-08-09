import re

from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_PHONE_RE = re.compile(r"(\+\d{7,15})")


class SmsOutreachAgent:
    """One-way outreach only: parses 'Text <+E164>: <message>' style requests
    and sends via the ACE SMS capability (Twilio creds stay in ACE, opt-outs
    enforced centrally). It never holds a conversation — inbound SMS flows
    through the ACE SMS channel to conversational agents instead."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        capabilities: AceCapabilityClient | None = None,
    ) -> None:
        self._config = config or get_agent_config()
        self._capabilities = capabilities or AceCapabilityClient(
            base_url=self._config.ace_base_url
        )

    async def handle(self, *, user_input: str, envelope: ContextEnvelope | None) -> str:
        if envelope is None:
            return f"[{self._config.display_name}] Caller context required."
        match = _PHONE_RE.search(user_input)
        _, _, message = user_input.partition(":")
        message = message.strip()
        if not match or not message:
            return (
                f"[{self._config.display_name}] Tell me who and what, e.g.: "
                "'Text +15551234567: Your appointment is tomorrow at 9am.'"
            )
        sid = await self._capabilities.sms_send(
            envelope=envelope,
            agent_key=self._config.agent_key,
            to_number=match.group(1),
            body=message,
        )
        return (
            f"[{self._config.display_name}] Sent to {match.group(1)} "
            f"(message sid: {sid})."
        )


class SmsOutreachAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = SmsOutreachAgent()

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
