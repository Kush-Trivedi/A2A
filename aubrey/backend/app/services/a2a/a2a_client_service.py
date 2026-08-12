"""Aubrey's single gateway for talking to remote A2A agents.

Chat and orchestration code never touch raw protocol objects — they consume
A2AStreamEvents. Errors always surface as typed aubrey errors. Hop
authentication (service tokens on the wire) lands with delegation at M5."""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.types import AgentCard, StreamResponse

from ...utils.common.logger import Logger
from .a2a_settings import A2ASettings, get_a2a_settings
from .artifact_mapper import ArtifactMapper, MappedArtifact
from .context_envelope import ContextEnvelope
from .dispatch_auditor import A2ADispatchAuditor, get_dispatch_auditor
from .error_translator import A2AErrorTranslator
from .message_factory import A2AMessageFactory
from .part_mapper import PartMapper
from .task_lifecycle import TaskLifecycleTracker

logger = Logger(__name__).get_logger()

_WELL_KNOWN_MARKER = "/.well-known/"


@dataclass(frozen=True)
class A2AStreamEvent:
    kind: str  # "text" | "artifact" | "state"
    text: str = ""
    artifact: MappedArtifact | None = None
    state: str = ""


class A2AClientService:
    def __init__(
        self,
        settings: A2ASettings | None = None,
        message_factory: A2AMessageFactory | None = None,
        part_mapper: PartMapper | None = None,
        artifact_mapper: ArtifactMapper | None = None,
        error_translator: A2AErrorTranslator | None = None,
        auditor: A2ADispatchAuditor | None = None,
    ) -> None:
        self._settings = settings or get_a2a_settings()
        self._factory = message_factory or A2AMessageFactory()
        self._parts = part_mapper or PartMapper()
        self._artifacts = artifact_mapper or ArtifactMapper(self._parts)
        self._errors = error_translator or A2AErrorTranslator()
        self._auditor = auditor or get_dispatch_auditor()
        self._card_cache: dict[str, tuple[AgentCard, float]] = {}

    @staticmethod
    def split_card_url(card_url: str) -> tuple[str, str]:
        """(base_url, relative_card_path) from a full card URL."""
        base, marker, rest = card_url.partition(_WELL_KNOWN_MARKER)
        if marker:
            return base, f"{marker}{rest}"
        return card_url.rstrip("/"), "/.well-known/agent-card.json"

    async def _resolve_card(
        self, httpx_client: httpx.AsyncClient, base_url: str, relative_card_path: str
    ) -> AgentCard:
        """Card fetch with TTL cache — no discovery round-trip per turn."""
        cache_key = f"{base_url}{relative_card_path}"
        cached = self._card_cache.get(cache_key)
        if cached is not None and (
            time.monotonic() - cached[1]
        ) < self._settings.card_cache_ttl_seconds:
            return cached[0]
        resolver = A2ACardResolver(
            httpx_client, base_url, agent_card_path=relative_card_path
        )
        card = await resolver.get_agent_card()
        self._card_cache[cache_key] = (card, time.monotonic())
        return card

    async def stream_message(
        self,
        *,
        agent_key: str,
        card_url: str,
        text: str,
        context_id: str,
        envelope: ContextEnvelope | None = None,
        reference_task_ids: tuple[str, ...] = (),
    ) -> AsyncIterator[A2AStreamEvent]:
        tracker = TaskLifecycleTracker(agent_key=agent_key)
        request = self._factory.send_request(
            text=text,
            context_id=context_id,
            envelope=envelope,
            reference_task_ids=reference_task_ids,
        )
        base_url, relative_card_path = self.split_card_url(card_url)
        seen_artifact_ids: set[str] = set()
        yielded_text = False
        call_failed = False

        httpx_client = httpx.AsyncClient(timeout=self._settings.request_timeout_seconds)
        client = None
        try:
            card = await self._resolve_card(httpx_client, base_url, relative_card_path)
            client = await create_client(
                card,
                ClientConfig(
                    streaming=self._settings.streaming_enabled,
                    httpx_client=httpx_client,
                ),
            )
            async for response in client.send_message(request):
                for event in self._map_response(response, tracker, seen_artifact_ids):
                    if event.kind == "text" and event.text:
                        yielded_text = True
                    yield event

            if not yielded_text and not tracker.needs_user_action:
                logger.warning("A2A agent produced no text output", extra=tracker.summary())
        except Exception as exc:  # noqa: BLE001 — translated to one typed error
            call_failed = True
            translated = self._errors.translate(exc, agent_key=agent_key)
            logger.error(
                "A2A call failed",
                extra={"agent_key": agent_key, "error_code": translated.code},
                exc_info=True,
            )
            raise translated from exc
        finally:
            if client is not None:
                await client.close()
            await httpx_client.aclose()
            if envelope is not None:
                await self._auditor.record_dispatch(
                    envelope=envelope,
                    agent_key=agent_key,
                    task_id=tracker.task_id,
                    final_state=self._outcome(tracker, yielded_text, call_failed),
                )
            logger.info("A2A turn finished", extra=tracker.summary())

    @staticmethod
    def _outcome(
        tracker: TaskLifecycleTracker, yielded_text: bool, call_failed: bool
    ) -> str:
        if call_failed:
            return "failed"
        if tracker.current_state != "unknown":
            return tracker.current_state
        # Message-only responses never open a task; a streamed answer with
        # no failure is a completed turn.
        return "completed" if yielded_text else "empty"

    def _map_response(
        self,
        response: StreamResponse,
        tracker: TaskLifecycleTracker,
        seen_artifact_ids: set[str],
    ) -> list[A2AStreamEvent]:
        events: list[A2AStreamEvent] = []
        payload = response.WhichOneof("payload")

        if payload == "message":
            text = self._parts.message_text(response.message)
            if text:
                events.append(A2AStreamEvent(kind="text", text=text))

        elif payload == "status_update":
            update = response.status_update
            tracker.record(update.status.state, task_id=update.task_id)
            status_text = (
                self._parts.message_text(update.status.message)
                if update.status.HasField("message")
                else ""
            )
            if status_text:
                events.append(A2AStreamEvent(kind="text", text=status_text))
            if tracker.needs_user_action:
                events.append(A2AStreamEvent(kind="state", state=tracker.current_state))

        elif payload == "artifact_update":
            update = response.artifact_update
            tracker.record_task_id(update.task_id)
            artifact = update.artifact
            if artifact.artifact_id not in seen_artifact_ids:
                seen_artifact_ids.add(artifact.artifact_id)
                events.append(
                    A2AStreamEvent(kind="artifact", artifact=self._artifacts.map(artifact))
                )

        elif payload == "task":
            task = response.task
            tracker.record(task.status.state, task_id=task.id)
            for artifact in task.artifacts:
                if artifact.artifact_id not in seen_artifact_ids:
                    seen_artifact_ids.add(artifact.artifact_id)
                    events.append(
                        A2AStreamEvent(
                            kind="artifact", artifact=self._artifacts.map(artifact)
                        )
                    )
            if task.status.HasField("message"):
                text = self._parts.message_text(task.status.message)
                if text:
                    events.append(A2AStreamEvent(kind="text", text=text))
            if tracker.needs_user_action:
                events.append(A2AStreamEvent(kind="state", state=tracker.current_state))

        return events


_service: A2AClientService | None = None


def get_a2a_client_service() -> A2AClientService:
    global _service
    if _service is None:
        _service = A2AClientService()
    return _service
