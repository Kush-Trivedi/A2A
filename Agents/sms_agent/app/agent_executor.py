"""SMS Agent — the channel-only agent (never on the chat UI).

Two jobs, one agent:
- INBOUND: a patient texts the platform number; the ACE SMS channel webhook
  routes the message here; the reply goes back over SMS (short, plain).
- OUTREACH: staff/systems ask it to send a one-way notification
  ('Text +15551234567: Your appointment is tomorrow at 9am.') via the ACE
  SMS capability — Twilio creds stay in ACE, opt-outs enforced centrally.
"""

import re

from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_PHONE_RE = re.compile(r"(\+\d{7,15})")
_SMS_MAX_CHARS = 480


class SmsAgent:
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
        if _PHONE_RE.search(user_input) and ":" in user_input:
            return await self._outreach(user_input, envelope)
        return await self._inbound_reply(user_input, envelope)

    async def _outreach(self, user_input: str, envelope: ContextEnvelope) -> str:
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

    async def _inbound_reply(self, question: str, envelope: ContextEnvelope) -> str:
        """Conversational reply for a patient's inbound text — LLM with the
        team's SMS prompt when configured, safe template otherwise. Always
        SMS-sized."""
        deployment = self._config.default_deployment
        system_prompt = self._config.prompt_store.get("system")
        if deployment and system_prompt is not None:
            try:
                answer = await self._capabilities.llm_chat(
                    envelope=envelope,
                    agent_key=self._config.agent_key,
                    deployment=deployment,
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt.format(
                                display_name=self._config.display_name
                            ),
                        },
                        {"role": "user", "content": question.strip()},
                    ],
                )
                if answer.strip():
                    return answer.strip()[:_SMS_MAX_CHARS]
            except Exception:  # noqa: BLE001 — LLM not configured: template reply
                pass
        fallback = self._config.prompt_store.get("fallback")
        if fallback is not None:
            return fallback.format(
                display_name=self._config.display_name, question=question.strip()
            ).strip()[:_SMS_MAX_CHARS]
        return (
            "Thanks for your message. Our care team will follow up. "
            "Reply STOP to opt out of texts."
        )


class TeamAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = SmsAgent()

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
        raise NotImplementedError("Cancellation is not supported by this agent.")
