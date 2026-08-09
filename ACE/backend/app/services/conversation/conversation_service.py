from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from ...entity.chat.chat_message_entity import ChatMessageEntity
from ...entity.chat.chat_session_entity import ChatSessionEntity
from ...llm.azure_foundry.ace_azure_foundry import AceAzureFoundry
from ...prompts import PromptRepository, get_prompt_repository
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.authorization.context_attrs import AuthorizationContextBuilder
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import AppError, BadRequestError, LLMError, PermissionDeniedError
from ace_agent_kit import ContextEnvelope

from ..a2a import A2AClientService, A2AStreamEvent, get_a2a_client_service
from ..a2a.dispatch_auditor import A2ADispatchAuditor
from ..agents import AgentDefinition, AgentRegistry, get_agent_registry
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service
from ..embedding.embedding_service import EmbeddingService, get_embedding_service
from ..knowledge import KnowledgeChunk
from ..knowledge.gateway import KnowledgeGateway, get_knowledge_gateway
from ..knowledge.settings import KnowledgeSettings, get_knowledge_settings
from .conversation_store import ROLE_ASSISTANT, ROLE_USER, ConversationStore, get_conversation_store
from .out_of_scope_responder import (
    OutOfScopeResponder,
    RefusalResponse,
    get_out_of_scope_responder,
)

logger = Logger(__name__).get_logger()

_HISTORY_TURN_LIMIT = 12


@dataclass(frozen=True)
class RetrievedSource:
    document_id: str
    source_name: str
    knowledge_source: str
    score: float


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    message_id: str
    agent_id: str
    answer: str
    sources: list[RetrievedSource] = field(default_factory=list)
    refusal: dict | None = None


class ConversationService:
    def __init__(
        self,
        store: ConversationStore | None = None,
        registry: AgentRegistry | None = None,
        gateway: KnowledgeGateway | None = None,
        embedding: EmbeddingService | None = None,
        llm: AceAzureFoundry | None = None,
        settings: KnowledgeSettings | None = None,
        prompts: PromptRepository | None = None,
        enforcer: CasbinEnforcer | None = None,
        a2a_client: A2AClientService | None = None,
        registry_service: AgentRegistryService | None = None,
        out_of_scope: OutOfScopeResponder | None = None,
    ) -> None:
        self._store = store or get_conversation_store()
        self._registry = registry or get_agent_registry()
        self._gateway = gateway or get_knowledge_gateway()
        self._embedding = embedding or get_embedding_service()
        self._llm = llm or AceAzureFoundry()
        self._settings = settings or get_knowledge_settings()
        self._prompts = prompts or get_prompt_repository()
        self._enforcer = enforcer or get_casbin_enforcer()
        self._a2a = a2a_client or get_a2a_client_service()
        self._registry_service = registry_service or get_agent_registry_service()
        self._out_of_scope = out_of_scope or get_out_of_scope_responder()

    async def list_sessions(self, *, context: SessionContext) -> list[ChatSessionEntity]:
        return await self._store.list_sessions(context=context)

    async def create_session(
        self, *, context: SessionContext, title: str
    ) -> ChatSessionEntity:
        return await self._store.create_session(context=context, title=title)

    async def rename_session(
        self, *, context: SessionContext, session_id: str, title: str
    ) -> ChatSessionEntity:
        return await self._store.rename_session(
            context=context, session_id=session_id, title=title
        )

    async def get_messages(
        self, *, context: SessionContext, session_id: str
    ) -> list[ChatMessageEntity]:
        return await self._store.list_messages(context=context, session_id=session_id)

    async def archive_session(
        self, *, context: SessionContext, session_id: str
    ) -> None:
        await self._store.archive_session(context=context, session_id=session_id)
        await self._gateway.soft_delete_session_uploads(
            tenant_id=context.tenant_id, session_id=session_id
        )


    async def send(
        self,
        *,
        context: SessionContext,
        agent_id: str | None,
        message: str,
        session_id: str | None = None,
    ) -> ChatTurnResult:
        prepared = await self._prepare_turn(
            context=context, agent_id=agent_id, message=message, session_id=session_id
        )
        if prepared.refusal is not None:
            return await self._finish_refused_turn(context=context, prepared=prepared)

        answer_parts: list[str] = []
        if prepared.is_a2a:
            async for event in self._a2a_events(context=context, prepared=prepared):
                if event.kind == "text" and event.text:
                    answer_parts.append(event.text)
        else:
            try:
                async for token in self._llm.astream_chat(messages=prepared.messages):
                    answer_parts.append(token)
            except Exception as exc:  # noqa: BLE001
                raise LLMError(cause=exc) from exc

        answer = "\n".join(answer_parts).strip() if prepared.is_a2a else "".join(answer_parts).strip()
        assistant = await self._persist_answer(
            context=context, session_id=prepared.session_id, agent=prepared.agent,
            answer=answer, sources=prepared.sources,
        )
        return ChatTurnResult(
            session_id=prepared.session_id,
            message_id=assistant.id,
            agent_id=prepared.agent.id,
            answer=answer,
            sources=prepared.sources,
        )

    async def stream(
        self,
        *,
        context: SessionContext,
        agent_id: str | None,
        message: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        try:
            prepared = await self._prepare_turn(
                context=context, agent_id=agent_id, message=message, session_id=session_id
            )
        except AppError as exc:
            yield {"event": "error", "data": {"code": exc.code, "message": exc.client_message()}}
            return

        yield {
            "event": "meta",
            "data": {
                "session_id": prepared.session_id,
                "agent_id": prepared.agent.id,
                "sources": [s.source_name for s in prepared.sources],
            },
        }

        if prepared.refusal is not None:
            result = await self._finish_refused_turn(context=context, prepared=prepared)
            yield {"event": "refusal", "data": prepared.refusal.to_dict()}
            yield {
                "event": "done",
                "data": {
                    "session_id": result.session_id,
                    "user_message_id": prepared.user_message_id,
                    "message_id": result.message_id,
                },
            }
            return

        answer_parts: list[str] = []
        if prepared.is_a2a:
            try:
                async for event in self._a2a_events(context=context, prepared=prepared):
                    if event.kind == "text" and event.text:
                        answer_parts.append(event.text)
                        yield {"event": "token", "data": {"text": event.text}}
                    elif event.kind == "artifact" and event.artifact is not None:
                        yield {"event": "artifact", "data": event.artifact.to_dict()}
                    elif event.kind == "state":
                        yield {"event": "state", "data": {"state": event.state}}
            except AppError as exc:
                yield {"event": "error", "data": {"code": exc.code, "message": exc.client_message()}}
                return
        else:
            try:
                async for token in self._llm.astream_chat(messages=prepared.messages):
                    answer_parts.append(token)
                    yield {"event": "token", "data": {"text": token}}
            except Exception as exc:  # noqa: BLE001
                logger.error("chat stream failed", extra={"error": str(exc)}, exc_info=True)
                yield {"event": "error", "data": {"code": "llm_error", "message": "The model response failed."}}
                return

        answer = "\n".join(answer_parts).strip() if prepared.is_a2a else "".join(answer_parts).strip()
        assistant = await self._persist_answer(
            context=context, session_id=prepared.session_id, agent=prepared.agent,
            answer=answer, sources=prepared.sources,
        )
        yield {
            "event": "done",
            "data": {
                "session_id": prepared.session_id,
                "user_message_id": prepared.user_message_id,
                "message_id": assistant.id,
            },
        }

    @dataclass(frozen=True)
    class _PreparedTurn:
        session_id: str
        user_message_id: str
        agent: AgentDefinition
        messages: list[dict[str, str]]
        sources: list[RetrievedSource]
        question: str = ""
        card_url: str | None = None
        auth_audience: str | None = None
        refusal: RefusalResponse | None = None

        @property
        def is_a2a(self) -> bool:
            return bool(self.card_url)

    async def _prepare_turn(
        self,
        *,
        context: SessionContext,
        agent_id: str | None,
        message: str,
        session_id: str | None,
    ) -> _PreparedTurn:
        question = (message or "").strip()
        if not question:
            raise BadRequestError("Message must not be empty.")

        agent, card_url, auth_audience = await self._resolve_agent(
            context=context, agent_id=agent_id
        )

        refusal: RefusalResponse | None = None
        try:
            await self._check_agent_permission(context=context, agent=agent)
        except PermissionDeniedError:
            refusal = await self._out_of_scope.for_denied_agent(
                tenant_id=context.tenant_id, agent_key=agent.id
            )

        if session_id:
            session = await self._store.get_owned_session(
                context=context, session_id=session_id
            )
        else:
            session = await self._store.create_session(
                context=context, title=question[:60]
            )
        resolved_session_id = session.id

        history = await self._store.list_messages(
            context=context, session_id=resolved_session_id
        )

        user_message = await self._store.add_message(
            context=context, session_id=resolved_session_id,
            role=ROLE_USER, content=question,
        )

        if refusal is not None or card_url:
            # Refused turns generate nothing; remote A2A agents own their
            # retrieval and prompting.
            messages, sources = [], []
        else:
            messages, sources = await self._build_generation(
                context=context, agent=agent, history=history,
                question=question, session_id=resolved_session_id,
            )
        return self._PreparedTurn(
            session_id=resolved_session_id,
            user_message_id=user_message.id,
            agent=agent,
            messages=messages,
            sources=sources,
            question=question,
            card_url=None if refusal is not None else card_url,
            auth_audience=auth_audience,
            refusal=refusal,
        )

    async def _resolve_agent(
        self, *, context: SessionContext, agent_id: str | None
    ) -> tuple[AgentDefinition, str | None, str | None]:
        """Registered A2A agents take precedence over built-in definitions."""
        if agent_id:
            registered = await self._registry_service.find_active_agent(
                tenant_id=context.tenant_id, key=agent_id
            )
            if registered is not None and registered.card_url:
                definition = AgentDefinition(
                    id=registered.agent_key,
                    display_name=registered.display_name,
                    description=registered.description,
                    aliases=tuple(registered.aliases or []),
                    knowledge_sources=tuple(registered.knowledge_sources or []),
                    include_session_uploads=False,
                    permission=registered.permission,
                    retrieval_mode=registered.retrieval_mode,
                )
                auth_audience = str(
                    (registered.team_config or {}).get("auth_audience") or ""
                ) or None
                return definition, registered.card_url, auth_audience
        return self._registry.resolve(agent_id), None, None

    async def _check_agent_permission(
        self, *, context: SessionContext, agent: AgentDefinition
    ) -> None:
        if not agent.permission or not self._enforcer.enabled:
            return
        allowed = await self._enforcer.enforce_any_role(
            context.roles,
            context.tenant_id,
            f"agent:{agent.id}",
            agent.permission,
            AuthorizationContextBuilder.build(context),
        )
        if not allowed:
            raise PermissionDeniedError(
                details={"agent_id": agent.id, "action": agent.permission}
            )

    async def _build_generation(
        self,
        *,
        context: SessionContext,
        agent: AgentDefinition,
        history: list[ChatMessageEntity],
        question: str,
        session_id: str,
    ) -> tuple[list[dict[str, str]], list[RetrievedSource]]:
        chunks = await self._retrieve(context=context, agent=agent, question=question,
                                      session_id=session_id)
        messages = self._build_messages(agent=agent, history=history, chunks=chunks,
                                        question=question)
        return messages, self._dedupe_sources(chunks)

    async def edit_message(
        self,
        *,
        context: SessionContext,
        session_id: str,
        message_id: str,
        content: str,
    ) -> ChatTurnResult:
        question = (content or "").strip()
        if not question:
            raise BadRequestError("Message must not be empty.")

        await self._store.get_owned_session(context=context, session_id=session_id)
        edited = await self._store.edit_user_message(
            context=context, session_id=session_id, message_id=message_id, content=question,
        )
        agent = self._registry.resolve(edited.previous_agent_id)
        await self._check_agent_permission(context=context, agent=agent)

        active = await self._store.list_messages(context=context, session_id=session_id)
        history = active[:-1]  # everything except the edited message we just inserted

        messages, sources = await self._build_generation(
            context=context, agent=agent, history=history,
            question=question, session_id=session_id,
        )

        answer_parts: list[str] = []
        try:
            async for token in self._llm.astream_chat(messages=messages):
                answer_parts.append(token)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(cause=exc) from exc

        answer = "".join(answer_parts).strip()
        assistant = await self._persist_answer(
            context=context, session_id=session_id, agent=agent,
            answer=answer, sources=sources,
        )
        await self._store.link_edit_version_assistant(
            version_id=edited.new_message.edit_version_id, assistant_message_id=assistant.id,
        )
        return ChatTurnResult(
            session_id=session_id, message_id=assistant.id,
            answer=answer, sources=sources, agent_id=agent.id,
        )

    async def set_feedback(
        self,
        *,
        context: SessionContext,
        session_id: str,
        message_id: str,
        feedback: str | None,
    ) -> ChatMessageEntity:
        return await self._store.set_feedback(
            context=context, session_id=session_id, message_id=message_id, feedback=feedback,
        )

    async def _retrieve(
        self,
        *,
        context: SessionContext,
        agent: AgentDefinition,
        question: str,
        session_id: str,
    ) -> list[KnowledgeChunk]:
        if not agent.uses_knowledge():
            return []
        try:
            embedding = await self._embedding.embed_query(question)
            return await self._gateway.retrieve(
                context=context,
                embedding=embedding,
                requested_sources=agent.knowledge_sources,
                session_id=session_id if agent.include_session_uploads else None,
                query_text=question,
                mode=agent.retrieval_mode or "",
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 — retrieval must not break chat
            logger.error("retrieval failed; continuing without context",
                         extra={"error": str(exc)}, exc_info=True)
            return []

    def _capabilities_block(self) -> str:
        lines: list[str] = []
        for agent in self._registry.list():
            if agent.id in ("default",):
                continue
            desc = agent.description or agent.display_name
            lines.append(f"- {agent.display_name}: {desc}")
        if not lines:
            return "- General assistance only."
        return "\n".join(lines)

    def _build_messages(
        self,
        *,
        agent: AgentDefinition,
        history: list[ChatMessageEntity],
        chunks: list[KnowledgeChunk],
        question: str,
    ) -> list[dict[str, str]]:
        system = None
        if agent.prompt_name:
            system = self._prompts.get(
                agent.prompt_name, capabilities=self._capabilities_block()
            )
        if not system:
            system = agent.system_prompt

        if chunks:
            context_block = "\n\n".join(
                f"[Source: {c.source_name}]\n{c.content}" for c in chunks
            )
            grounded = self._prompts.get(
                "chat.grounding", system=system, context=context_block
            )
            system = grounded or (
                f"{system}\n\n--- Retrieved context ---\n{context_block}"
            )
        elif not agent.strict_grounding:
            no_context = self._prompts.get("chat.no_context", system=system)
            if no_context:
                system = no_context

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for msg in history[-_HISTORY_TURN_LIMIT:]:
            role = msg.role if msg.role in (ROLE_USER, ROLE_ASSISTANT) else ROLE_USER
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": ROLE_USER, "content": question})
        return messages

    @staticmethod
    def _dedupe_sources(chunks: list[KnowledgeChunk]) -> list[RetrievedSource]:
        seen: set[str] = set()
        sources: list[RetrievedSource] = []
        for chunk in chunks:
            if chunk.document_id in seen:
                continue
            seen.add(chunk.document_id)
            sources.append(
                RetrievedSource(
                    document_id=chunk.document_id,
                    source_name=chunk.source_name,
                    knowledge_source=chunk.knowledge_source,
                    score=chunk.score,
                )
            )
        return sources

    async def _finish_refused_turn(
        self, *, context: SessionContext, prepared: "_PreparedTurn"
    ) -> ChatTurnResult:
        refusal = prepared.refusal
        assert refusal is not None
        assistant = await self._store.add_message(
            context=context,
            session_id=prepared.session_id,
            role=ROLE_ASSISTANT,
            content=refusal.message,
            metadata={
                "agent_id": prepared.agent.id,
                "refusal": refusal.to_dict(),
            },
        )
        logger.info(
            "Chat turn refused",
            extra={
                "agent_id": prepared.agent.id,
                "refusal_type": refusal.type,
                "team_key": refusal.team_key,
            },
        )
        return ChatTurnResult(
            session_id=prepared.session_id,
            message_id=assistant.id,
            agent_id=prepared.agent.id,
            answer=refusal.message,
            refusal=refusal.to_dict(),
        )

    def _a2a_events(
        self, *, context: SessionContext, prepared: "_PreparedTurn"
    ) -> AsyncIterator[A2AStreamEvent]:
        envelope = ContextEnvelope(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            user_id=context.user_id,
            roles=tuple(context.roles),
            correlation_id=A2ADispatchAuditor.new_correlation_id(),
            chat_session_id=prepared.session_id,
            purpose="chat",
        )
        return self._a2a.stream_message(
            agent_key=prepared.agent.id,
            card_url=prepared.card_url or "",
            text=prepared.question,
            context_id=prepared.session_id,
            envelope=envelope,
            auth_audience=prepared.auth_audience,
        )

    async def _persist_answer(
        self,
        *,
        context: SessionContext,
        session_id: str,
        agent: AgentDefinition,
        answer: str,
        sources: list[RetrievedSource],
    ) -> ChatMessageEntity:
        return await self._store.add_message(
            context=context,
            session_id=session_id,
            role=ROLE_ASSISTANT,
            content=answer,
            metadata={
                "agent_id": agent.id,
                "sources": [
                    {"document_id": s.document_id, "source_name": s.source_name,
                     "knowledge_source": s.knowledge_source, "score": round(s.score, 4)}
                    for s in sources
                ],
            },
        )


_service: ConversationService | None = None


def get_conversation_service() -> ConversationService:
    global _service
    if _service is None:
        _service = ConversationService()
    return _service
