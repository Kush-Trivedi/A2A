"""One chat turn, end to end, streamed:

    persist user message -> route (sticky-aware, Casbin-gated) -> build the
    token-budgeted window -> dispatch over A2A with the context envelope ->
    re-yield tokens/artifacts/states as they arrive -> persist the answer.

Yields (event, payload) pairs; the route frames them as SSE. Event names:
meta, token, artifact, state, disambiguation, refusal, error, done."""

import uuid
from collections.abc import AsyncIterator

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import OdtTeamEntity
from ...entity.chat import MessageKind, MessageRole
from ...security.session import SessionContext
from ...services.a2a import A2AClientService, ContextEnvelope, get_a2a_client_service
from ...services.agents.question_router_service import (
    QuestionRouterService,
    RouteAction,
    RouteCandidate,
    RouteDecision,
    get_question_router_service,
)
from ...utils.common.logger import Logger
from ...utils.errors import AppError, ValidationError
from .memory_window import MemoryWindowBuilder, get_memory_window_builder
from .session_service import ChatSessionService, get_chat_session_service

logger = Logger(__name__).get_logger()


class ConversationService:
    def __init__(
        self,
        sessions: ChatSessionService | None = None,
        router: QuestionRouterService | None = None,
        a2a: A2AClientService | None = None,
        windows: MemoryWindowBuilder | None = None,
    ) -> None:
        self._sessions = sessions or get_chat_session_service()
        self._router = router or get_question_router_service()
        self._a2a = a2a or get_a2a_client_service()
        self._windows = windows or get_memory_window_builder()
        self._db = get_postgres_connector()

    async def stream(
        self,
        *,
        context: SessionContext,
        question: str,
        session_id: str | None = None,
        agent_key: str | None = None,
        message_id: str | None = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        cleaned = (question or "").strip()
        if not cleaned:
            raise ValidationError("The question must not be empty.")

        if session_id:
            session = await self._sessions.get_owned_session(
                context=context, session_id=session_id
            )
        else:
            session = await self._sessions.create_session(context=context)

        messages = await self._sessions.list_messages(context=context, session_id=session.id)
        if message_id:
            message_index = next(
                (i for i, m in enumerate(messages) if m.id == message_id),
                None
            )
            if message_index is None:
                raise ValidationError("Message to replay was not found")
            user_message = messages[message_index]
            if user_message.role != MessageRole.USER:
                raise ValidationError("Message to replay is not a user message.")
            if user_message.content != cleaned:
                raise ValidationError("Message content does not match the provided question.")
            history = messages[:message_index]
        else:
            history = messages
            user_message = await self._sessions.append_message(
                context=context, session_id=session.id,
                role=MessageRole.USER, content=cleaned,
            )
        sticky = await self._sessions.sticky_agent(
            tenant_id=context.tenant_id, session_id=session.id
        )
        decision = await self._router.route(
            context=context, question=cleaned,
            sticky_agent=sticky, requested_agent=agent_key,
        )

        if decision.action in (RouteAction.DISPATCH, RouteAction.FALLBACK):
            async for event in self._dispatch(
                context=context, session_id=session.id,
                question=cleaned, history=history, decision=decision,
                user_message_id=user_message.id,
            ):
                yield event
            return

        if decision.action == RouteAction.DISAMBIGUATE:
            payload = {
                "candidates": [
                    {"agent_key": c.agent_key, "display_name": c.display_name}
                    for c in decision.candidates
                ]
            }
            await self._sessions.append_message(
                context=context, session_id=session.id,
                role=MessageRole.ASSISTANT, content="",
                metadata={"kind": MessageKind.ROUTING, "disambiguation": payload},
            )
            yield "disambiguation", payload
            yield "done", {"session_id": session.id, "user_message_id": user_message.id}
            return

        # REFUSAL_INACCESSIBLE — matched but role-blocked, or nothing routable
        payload = await self._refusal_payload(context.tenant_id, decision)
        await self._sessions.append_message(
            context=context, session_id=session.id,
            role=MessageRole.ASSISTANT, content="",
            metadata={"kind": MessageKind.ROUTING, "refusal": payload},
        )
        yield "refusal", payload
        yield "done", {"session_id": session.id, "user_message_id": user_message.id}

    async def _dispatch(
        self,
        *,
        context: SessionContext,
        session_id: str,
        question: str,
        history: list,
        decision: RouteDecision,
        user_message_id: str,
    ) -> AsyncIterator[tuple[str, dict]]:
        agent = decision.matched
        yield "meta", {
            "session_id": session_id,
            "agent_key": agent.agent_key,
            "display_name": agent.display_name,
            "routing": decision.action,
        }
        if not agent.card_url:
            yield "error", {
                "code": "agent_card_missing",
                "message": f"Agent '{agent.agent_key}' has no card_url registered.",
            }
            return

        window = self._windows.build(history)
        envelope = ContextEnvelope(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            actor_id=context.actor_id,
            roles=tuple(context.roles),
            session_id=session_id,
            correlation_id=uuid.uuid4().hex,
            window=tuple({"role": w.role, "content": w.content} for w in window),
        )

        parts: list[str] = []
        try:
            async for event in self._a2a.stream_message(
                agent_key=agent.agent_key,
                card_url=agent.card_url,
                text=question,
                context_id=session_id,
                envelope=envelope,
            ):
                if event.kind == "text" and event.text:
                    parts.append(event.text)
                    yield "token", {"text": event.text}
                elif event.kind == "artifact" and event.artifact is not None:
                    yield "artifact", event.artifact.to_dict()
                elif event.kind == "state" and event.state:
                    yield "state", {"state": event.state}
        except AppError as exc:
            yield "error", {"code": exc.code, "message": exc.client_message()}
            return

        answer = "".join(parts)
        message = await self._sessions.append_message(
            context=context, session_id=session_id,
            role=MessageRole.ASSISTANT, content=answer,
            metadata={"kind": MessageKind.ANSWER, "agent_key": agent.agent_key},
        )
        yield "done", {"session_id": session_id,"user_message_id": user_message_id, "message_id": message.id}

    async def _refusal_payload(
        self, tenant_id: str, decision: RouteDecision
    ) -> dict:
        """Facts only — team name and contact come from the registry, and the
        surface (or an LLM prompt) renders them. No canned English here."""
        matched: RouteCandidate | None = decision.matched
        if matched is None:
            return {"reason": "no_accessible_agent"}
        payload: dict = {
            "reason": "role_blocked",
            "agent_key": matched.agent_key,
            "display_name": matched.display_name,
            "team_key": matched.team_key,
        }
        async with self._db.session() as session:
            team = (
                await session.exec(
                    select(OdtTeamEntity).where(
                        OdtTeamEntity.tenant_id == tenant_id,
                        OdtTeamEntity.key == matched.team_key,
                    )
                )
            ).first()
        if team is not None:
            payload["team_name"] = team.name
            payload["contact_email"] = team.contact_email or ""
        return payload


_service: ConversationService | None = None


def get_conversation_service() -> ConversationService:
    global _service
    if _service is None:
        _service = ConversationService()
    return _service
