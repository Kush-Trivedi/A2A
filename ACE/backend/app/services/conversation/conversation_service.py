import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from ...entity.chat.chat_message_entity import ChatMessageEntity
from ...entity.chat.chat_session_entity import ChatSessionEntity
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.authorization.context_attrs import AuthorizationContextBuilder
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import AppError, BadRequestError, NotFoundError, LLMError, PermissionDeniedError
from ace_agent_kit import ContextEnvelope

from ..a2a import A2AClientService, A2AStreamEvent, get_a2a_client_service
from ..a2a.dispatch_auditor import A2ADispatchAuditor
from ..agents import AgentDefinition
from ..agents.question_router_service import (
    QuestionRouterService,
    RouteAction,
    get_question_router_service,
)
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service
from ..knowledge.gateway import KnowledgeGateway, get_knowledge_gateway
from .conversation_store import ROLE_ASSISTANT, ROLE_USER, ConversationStore, get_conversation_store
from .out_of_scope_responder import (
    OutOfScopeResponder,
    RefusalResponse,
    get_out_of_scope_responder,
)

logger = Logger(__name__).get_logger()


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
    disambiguation: dict | None = None


class ConversationService:
    def __init__(
        self,
        store: ConversationStore | None = None,
        gateway: KnowledgeGateway | None = None,
        enforcer: CasbinEnforcer | None = None,
        a2a_client: A2AClientService | None = None,
        registry_service: AgentRegistryService | None = None,
        out_of_scope: OutOfScopeResponder | None = None,
        router: QuestionRouterService | None = None,
    ) -> None:
        self._store = store or get_conversation_store()
        self._gateway = gateway or get_knowledge_gateway()
        self._enforcer = enforcer or get_casbin_enforcer()
        self._a2a = a2a_client or get_a2a_client_service()
        self._registry_service = registry_service or get_agent_registry_service()
        self._out_of_scope = out_of_scope or get_out_of_scope_responder()
        self._router = router or get_question_router_service()

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
        if prepared.disambiguation is not None:
            return await self._finish_disambiguation_turn(context=context, prepared=prepared)

        answer_parts: list[str] = []
        async for event in self._a2a_events(context=context, prepared=prepared):
            if event.kind == "text" and event.text:
                answer_parts.append(event.text)

        # Agents stream token chunks — concatenate verbatim, never re-join.
        answer = "".join(answer_parts).strip()
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

        if prepared.disambiguation is not None:
            result = await self._finish_disambiguation_turn(
                context=context, prepared=prepared
            )
            yield {"event": "disambiguation", "data": prepared.disambiguation}
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

        # Agents stream token chunks — concatenate verbatim, never re-join.
        answer = "".join(answer_parts).strip()
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
        sources: list[RetrievedSource]
        question: str = ""
        card_url: str | None = None
        auth_audience: str | None = None
        refusal: RefusalResponse | None = None
        disambiguation: dict | None = None

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

        requested = (agent_id or "").strip().lower()
        refusal: RefusalResponse | None = None
        disambiguation: dict | None = None
        agent: AgentDefinition | None = None
        card_url: str | None = None
        auth_audience: str | None = None

        if not requested or requested == "auto":
            # Supervisor-free routing: one embedding + one lookup decides the
            # agent; an explicit catalog pick (agent_id set) always wins.
            sticky = await self._sticky_agent(context=context, session_id=session_id)
            decision = await self._router.route(
                context=context, question=question, sticky_agent=sticky
            )
            if decision.action is RouteAction.REFUSAL_INACCESSIBLE and decision.matched_agent:
                refusal = await self._out_of_scope.for_denied_agent(
                    tenant_id=context.tenant_id, agent_key=decision.matched_agent
                )
                agent = AgentDefinition(
                    id=decision.matched_agent,
                    display_name=decision.matched_agent,
                    description="",
                )
            elif decision.action is RouteAction.DISAMBIGUATE:
                disambiguation = {
                    "message": "I can route this to more than one assistant — which one?",
                    "candidates": [
                        {"agent_key": c.agent_key, "display_name": c.display_name}
                        for c in decision.candidates
                    ],
                }
                agent = AgentDefinition(
                    id="router", display_name="Assistant Router", description=""
                )
            else:
                requested = decision.agent_key or self._router.fallback_agent

        if agent is None:
            agent, card_url, auth_audience = await self._resolve_agent(
                context=context, agent_id=requested
            )
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

        user_message = await self._store.add_message(
            context=context, session_id=resolved_session_id,
            role=ROLE_USER, content=question,
        )

        # Registered A2A agents produce every answer — ACE does no local
        # generation. History stays persisted for the UI; the agent gets
        # session continuity via context_id.
        return self._PreparedTurn(
            session_id=resolved_session_id,
            user_message_id=user_message.id,
            agent=agent,
            sources=[],
            question=question,
            card_url=None if (refusal is not None or disambiguation is not None) else card_url,
            auth_audience=auth_audience,
            refusal=refusal,
            disambiguation=disambiguation,
        )

    async def _sticky_agent(
        self, *, context: SessionContext, session_id: str | None
    ) -> str | None:
        """The agent already carrying this conversation — follow-ups stay
        with it unless a new question clearly belongs to another agent."""
        if not session_id:
            return None
        try:
            messages = await self._store.list_messages(
                context=context, session_id=session_id
            )
        except AppError:
            return None
        for message in reversed(messages):
            if message.role != ROLE_ASSISTANT:
                continue
            try:
                metadata = json.loads(message.metadata_json or "{}")
            except ValueError:
                continue
            agent_key = str(metadata.get("agent_id") or "")
            if agent_key and agent_key != "router":
                return agent_key
        return None

    async def _resolve_agent(
        self, *, context: SessionContext, agent_id: str | None
    ) -> tuple[AgentDefinition, str | None, str | None]:
        """Registered A2A agents take precedence over built-in definitions."""
        requested = (agent_id or "").strip()
        if not requested:
            raise BadRequestError("Select an available assistant before sending a message.")

        registered = await self._registry_service.find_active_agent(
            tenant_id=context.tenant_id, key=requested
        )

        if registered is None or not registered.card_url:
            raise NotFoundError(
                "Agent not found or missing card URL.",
                details={"requested_agent_id": requested or None}
            )

        definition = AgentDefinition(
            id=registered.agent_key,
            display_name=registered.display_name,
            description=registered.description,
            aliases=tuple(registered.aliases or []),
            knowledge_sources=tuple(registered.knowledge_sources or []),
            permission=registered.permission,
            retrieval_mode=registered.retrieval_mode,
        )
        auth_audience = str(
            (registered.team_config or {}).get("auth_audience") or ""
        ) or None
        return definition, registered.card_url, auth_audience

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
        agent, card_url, auth_audience = await self._resolve_agent(
            context=context, agent_id=edited.previous_agent_id
        )
        await self._check_agent_permission(context=context, agent=agent)

        answer_parts: list[str] = []
        try:
            prepared = self._PreparedTurn(
                session_id=session_id,
                user_message_id=edited.new_message.id,
                agent=agent,
                sources=[],
                question=question,
                card_url=card_url,
                auth_audience=auth_audience,
            )
            async for event in self._a2a_events(context=context, prepared=prepared):
                if event.kind == "text" and event.text:
                    answer_parts.append(event.text)
        except AppError:
            raise
        except Exception as exc:
            raise LLMError(cause=exc) from exc

        answer = "".join(answer_parts).strip()
        assistant = await self._persist_answer(
            context=context, session_id=session_id, agent=agent,
            answer=answer, sources=[]
        )
        await self._store.link_edit_version_assistant(
            version_id=edited.new_message.edit_version_id, assistant_message_id=assistant.id,
        )
        return ChatTurnResult(
            session_id=session_id, message_id=assistant.id, answer=answer,
            sources=[], agent_id=agent.id,
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

    async def _finish_disambiguation_turn(
        self, *, context: SessionContext, prepared: "_PreparedTurn"
    ) -> ChatTurnResult:
        payload = prepared.disambiguation or {}
        candidate_names = " or ".join(
            str(c.get("display_name", "")) for c in payload.get("candidates", [])
        )
        answer = str(payload.get("message") or f"Did you mean {candidate_names}?")
        assistant = await self._store.add_message(
            context=context,
            session_id=prepared.session_id,
            role=ROLE_ASSISTANT,
            content=answer,
            metadata={"agent_id": "router", "disambiguation": payload},
        )
        return ChatTurnResult(
            session_id=prepared.session_id,
            message_id=assistant.id,
            agent_id="router",
            answer=answer,
            disambiguation=payload,
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
