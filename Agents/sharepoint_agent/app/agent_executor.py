from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope, RetrievedChunk

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_MAX_CHUNKS = 4
_SNIPPET_CHARS = 400


class SharePointQaAgent:
    """Source-strict SharePoint Q&A.

    Retrieval is limited to the knowledge sources declared in agent.yaml
    (loaded by the team via /api/v1/knowledge/ingest/sharepoint). The
    KnowledgeGateway additionally filters those sources by the CALLER's
    Casbin read policies — a user without access to a source gets nothing
    from it, even through this agent.
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
        question = user_input.strip() or "(empty question)"
        if envelope is None:
            return (
                f"[{self._config.display_name}] I need caller context to check "
                "your document access. Please ask again from the chat screen."
            )

        chunks = await self._capabilities.retrieve(
            envelope=envelope,
            query=question,
            knowledge_sources=self._config.knowledge_sources,
            retrieval_mode=self._config.retrieval_mode,
        )
        if not chunks:
            sources = ", ".join(self._config.knowledge_sources)
            return (
                f"[{self._config.display_name}] I found no documents you can "
                f"access in my library ({sources}). Either nothing matches your "
                "question, or your role does not have read access to these "
                "sources — the Clinical Care team can grant it."
            )
        return self._grounded_answer(question, chunks)

    def _grounded_answer(self, question: str, chunks: list[RetrievedChunk]) -> str:
        lines = [
            f"[{self._config.display_name}] From the policy library, for: {question}"
        ]
        for chunk in chunks[:_MAX_CHUNKS]:
            snippet = chunk.content.strip()
            if len(snippet) > _SNIPPET_CHARS:
                snippet = snippet[:_SNIPPET_CHARS].rstrip() + "…"
            lines.append(f"\nFrom **{chunk.source_name}**:\n> {snippet}")
        lines.append("\nI only answer from the ingested SharePoint policy library.")
        return "\n".join(lines)


class SharePointQaAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = SharePointQaAgent()

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
