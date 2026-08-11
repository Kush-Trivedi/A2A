"""Retrieval agent template — the standard team agent shape.

LLM-FIRST AND STREAMING: retrieve (agent-bound sources, caller's Casbin
scope) -> stream tokens from the team's LLM deployment through ACE's
gateway -> each token is forwarded over the A2A stream as it arrives, so
the user sees the answer being written. The grounded-snippet fallback runs
ONLY while the LLM deployment is not configured (pre-creds parity) — no
answer is ever hardcoded.
"""

from collections.abc import AsyncIterator
from dataclasses import replace

from google.protobuf import json_format

from ace_agent_kit import (
    AceCapabilityClient,
    AgentDelegator,
    ContextEnvelope,
    DelegationTarget,
    RetrievedChunk,
)

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_MAX_CHUNKS = 4
_SNIPPET_CHARS = 400


class RetrievalAgent:
    def __init__(
        self,
        config: AgentConfig | None = None,
        capabilities: AceCapabilityClient | None = None,
    ) -> None:
        self._config = config or get_agent_config()
        self._capabilities = capabilities or AceCapabilityClient(
            base_url=self._config.ace_base_url
        )

    async def answer_stream(
        self, *, user_input: str, envelope: ContextEnvelope | None
    ) -> AsyncIterator[str]:
        """Yields answer chunks: token-by-token from the LLM, or a single
        grounded chunk while the deployment is not configured yet."""
        question = user_input.strip() or "(empty question)"
        if envelope is None:
            yield (
                f"[{self._config.display_name}] I need caller context to check "
                "your document access. Please ask again from the chat screen."
            )
            return

        chunks = await self._capabilities.retrieve(
            envelope=envelope,
            query=question,
            knowledge_sources=self._config.knowledge_sources,
            retrieval_mode=self._config.retrieval_mode,
            agent_key=self._config.agent_key,
        )
        if not chunks:
            # Nothing in MY sources — dynamically consult a configured peer
            # (manifest delegations.consult) so the user still gets an answer.
            consulted = await self._consult_peers(question, envelope)
            if consulted is not None:
                yield consulted
                return
            sources = ", ".join(self._config.knowledge_sources) or "my knowledge base"
            yield (
                f"[{self._config.display_name}] I found no documents you can "
                f"access in {sources}. Either nothing matches your question, or "
                "your role does not have read access — my owning team can grant it."
            )
            return

        streamed_any = False
        try:
            async for token in self._llm_token_stream(question, chunks, envelope):
                streamed_any = True
                yield token
        except Exception:  # noqa: BLE001 — LLM not configured yet: grounded fallback
            streamed_any = False
        if not streamed_any:
            yield self._grounded_answer(question, chunks)

    def _llm_token_stream(
        self, question: str, chunks: list[RetrievedChunk], envelope: ContextEnvelope
    ) -> AsyncIterator[str]:
        deployment = self._config.default_deployment
        system_prompt = self._config.prompt_store.get("system")
        if not deployment or system_prompt is None:
            raise RuntimeError("LLM deployment not configured.")
        context_block = "\n\n".join(
            f"[Source: {c.source_name}]\n{c.content}" for c in chunks[:_MAX_CHUNKS]
        )
        return self._capabilities.llm_chat_stream(
            envelope=envelope,
            agent_key=self._config.agent_key,
            deployment=deployment,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt.format(
                        display_name=self._config.display_name
                    )
                    + "\n\nAnswer ONLY from this retrieved context:\n"
                    + context_block,
                },
                {"role": "user", "content": question},
            ],
        )

    async def _consult_peers(
        self, question: str, envelope: ContextEnvelope
    ) -> str | None:
        """Dynamic agent-to-agent A2A call: peers come from the manifest
        (`delegations.consult` — KEYS only, never URLs), the card URL is
        resolved through ACE at runtime with the END USER's authorization
        enforced, and the envelope is forwarded with delegation provenance.
        Returns the peer's attributed answer, or None to fall through."""
        peer_keys = [
            str(key)
            for key in (self._config.delegations.get("consult") or [])
            if str(key).strip()
        ]
        for peer_key in peer_keys:
            try:
                peer = await self._capabilities.resolve_agent(
                    envelope=envelope, agent_key=peer_key
                )
                if not peer.found:
                    continue
                if not peer.accessible:
                    return (
                        f"[{self._config.display_name}] This looks like a question "
                        f"for **{peer.display_name}**, but your role does not have "
                        f"access — the {peer.team_key} team can grant it."
                    )
                delegated = replace(
                    envelope,
                    delegated_from=self._config.agent_key,
                    delegation_reason=f"No match in {self._config.agent_key} sources",
                )
                result = await AgentDelegator().delegate(
                    target=DelegationTarget(
                        capability="consult",
                        card_url=peer.card_url,
                        audience=peer.auth_audience,
                    ),
                    text=question,
                    envelope=delegated,
                    context_id=envelope.chat_session_id or "consult",
                )
                if result.text.strip():
                    return (
                        f"[{self._config.display_name} → consulted "
                        f"{peer.display_name}]\n{result.text.strip()}"
                    )
            except Exception:  # noqa: BLE001 — consult is best-effort, never fatal
                continue
        return None

    def _grounded_answer(self, question: str, chunks: list[RetrievedChunk]) -> str:
        lines = [f"[{self._config.display_name}] From my documents, for: {question}"]
        for chunk in chunks[:_MAX_CHUNKS]:
            snippet = chunk.content.strip()
            if len(snippet) > _SNIPPET_CHARS:
                snippet = snippet[:_SNIPPET_CHARS].rstrip() + "…"
            lines.append(f"\nFrom **{chunk.source_name}**:\n> {snippet}")
        lines.append("\nI only answer from my team's ingested documents.")
        return "\n".join(lines)


class TeamAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = RetrievalAgent()

    @staticmethod
    def _envelope_from(context: RequestContext) -> ContextEnvelope | None:
        if context.message is None:
            return None
        metadata = json_format.MessageToDict(context.message.metadata)
        return ContextEnvelope.from_metadata(metadata)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        async for chunk in self._agent.answer_stream(
            user_input=context.get_user_input(),
            envelope=self._envelope_from(context),
        ):
            await event_queue.enqueue_event(
                new_text_message(
                    chunk, context_id=context.context_id, task_id=context.task_id
                )
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this agent.")
