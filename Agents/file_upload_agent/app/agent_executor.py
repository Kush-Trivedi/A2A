"""File Q&A — STRICT session-scoped document answers, streamed.

The user uploads a file in the chat UI (📎); it lands in pgvector tagged
with the chat session. Retrieval is limited to THAT session — never shared
sources, never another session. Answers stream token-by-token from the
team's LLM deployment (via ACE's gateway); the grounded-snippet fallback
runs only while the deployment is not configured.
"""

from collections.abc import AsyncIterator

from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope, RetrievedChunk

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_MAX_CHUNKS = 4
_SNIPPET_CHARS = 400


class FileQaAgent:
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
        question = user_input.strip() or "(empty question)"
        if envelope is None or not envelope.chat_session_id:
            yield (
                f"[{self._config.display_name}] I need a chat session context "
                "to find your uploaded files. Please ask again from the chat screen."
            )
            return

        chunks = await self._capabilities.retrieve(
            envelope=envelope,
            query=question,
            knowledge_sources=(),  # session-strict: never a shared source
            session_id=envelope.chat_session_id,
            retrieval_mode=self._config.retrieval_mode,
            agent_key=self._config.agent_key,
        )
        if not chunks:
            yield (
                f"[{self._config.display_name}] I don't see any documents uploaded "
                "in this chat yet — please upload a file (Word, PowerPoint, Excel, "
                "PDF and more are supported) and ask again. I only answer from "
                "your uploaded files."
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
            f"[File: {c.source_name}]\n{c.content}" for c in chunks[:_MAX_CHUNKS]
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
                    + "\n\nAnswer ONLY from the user's uploaded files below. "
                    "If the answer is not in them, say so.\n" + context_block,
                },
                {"role": "user", "content": question},
            ],
        )

    def _grounded_answer(self, question: str, chunks: list[RetrievedChunk]) -> str:
        lines = [
            f"[{self._config.display_name}] Grounded strictly in your uploaded "
            f"file(s), here is what I found for: {question}"
        ]
        for chunk in chunks[:_MAX_CHUNKS]:
            snippet = chunk.content.strip()
            if len(snippet) > _SNIPPET_CHARS:
                snippet = snippet[:_SNIPPET_CHARS].rstrip() + "…"
            lines.append(f"\nFrom **{chunk.source_name}**:\n> {snippet}")
        lines.append("\nI only answer from documents uploaded in this chat session.")
        return "\n".join(lines)


class TeamAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = FileQaAgent()

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
