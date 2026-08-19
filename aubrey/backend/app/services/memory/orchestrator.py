"""The one entry point for memory — every channel (web, SMS, later
Teams/voice) calls assemble() before dispatch and commit() after the
answer, with nothing else varying per channel (NEW_PLAN §3).

No single-point bottleneck by construction: assemble embeds the question
ONCE, then runs every enabled layer in asyncio.gather under per-layer
deadlines — a slow or failing layer contributes nothing this turn and the
reply is never blocked on memory. commit() is the background half
(summary roll, fact/prospect extraction, periodic episode) and never
raises: a memory write failure is a log line, not a failed turn.
Redaction is enforced INSIDE the write path (layer.record and the summary
store), so no future caller can persist unredacted content by forgetting
a step.

M10c: MemoryPolicy.for_scope(scope) is consulted on BOTH assemble and
commit — external subjects (sms:/voice: user ids, §8.3) recall and write
only through their yaml-configured layer set; everything else is a
structural no-op. Episodes are written on a turn-count schedule (yaml
episodic_min_turns / episodic_every_turns) from the rolling summary,
through the episodic layer's redact->embed->encrypt path."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.chat import ChatMessageEntity
from ...entity.memory import SessionSummaryEntity
from ...utils.common.logger import Logger
from ...utils.crypto import FieldEncryptor, get_field_encryptor
from ...utils.errors import DatabaseError
from .extractor import MemoryExtractor, get_memory_extractor
from .layer import MemoryLayer, RecallQuery
from .layers import (
    get_episodic_memory_layer,
    get_prospective_memory_layer,
    get_semantic_memory_layer,
)
from .layers.working_memory import WorkingMemoryLayer
from .policy import MemoryPolicy
from .record import MemoryRecord
from .redactor import MemoryRedactor, get_memory_redactor
from .scope import MemoryScope
from .settings import MemorySettings, get_memory_settings
from .summarizer import SessionSummarizer, get_session_summarizer

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class MemoryBundle:
    """What one turn's memory looks like, decrypted and envelope-ready."""

    question: str
    window: tuple[dict[str, str], ...] = ()
    summary: str = ""
    facts: tuple[str, ...] = ()
    episodes: tuple[str, ...] = ()
    prospects: tuple[str, ...] = ()

    def memory_block(self) -> dict[str, Any]:
        """The envelope `memory` dict — empty parts omitted so agents that
        key on presence see only real content."""
        block: dict[str, Any] = {}
        if self.summary:
            block["summary"] = self.summary
        if self.facts:
            block["facts"] = list(self.facts)
        if self.episodes:
            block["episodes"] = list(self.episodes)
        if self.prospects:
            block["prospects"] = list(self.prospects)
        return block


class MemoryOrchestrator:
    def __init__(
        self,
        settings: MemorySettings | None = None,
        encryptor: FieldEncryptor | None = None,
        redactor: MemoryRedactor | None = None,
        summarizer: SessionSummarizer | None = None,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        self._settings = settings or get_memory_settings()
        self._encryptor = encryptor or get_field_encryptor()
        self._redactor = redactor or get_memory_redactor()
        self._summarizer = summarizer or get_session_summarizer()
        self._extractor = extractor or get_memory_extractor()
        self._db = get_postgres_connector()
        self._background: set[asyncio.Task] = set()

        available: dict[str, MemoryLayer] = {}
        for name in self._settings.layers_enabled:
            if name == "working":
                available[name] = WorkingMemoryLayer(settings=self._settings)
            elif name == "semantic":
                available[name] = get_semantic_memory_layer()
            elif name == "episodic":
                available[name] = get_episodic_memory_layer()
            elif name == "prospective":
                available[name] = get_prospective_memory_layer()
            else:
                # procedural lands in a later phase; an unknown yaml name
                # is a warning, never a crash.
                logger.warning("Unknown memory layer in layers_enabled: %s", name)
        self._layers = available

    # -- read side --------------------------------------------------------- #

    async def assemble(
        self, scope: MemoryScope, question: str, *, window_tokens: int | None = None
    ) -> MemoryBundle:
        budget = window_tokens if window_tokens is not None else self._settings.window_tokens
        # §8.3: the policy decides which layers this scope may recall from —
        # external subjects default to the live session only.
        policy = MemoryPolicy.for_scope(scope)
        names = [name for name in self._layers if policy.allows_layer(name)]
        query = RecallQuery(
            text=question, vector=await self._question_vector(question, names)
        )

        recalls = [
            self._guarded(name, self._layers[name].recall(scope, query, budget))
            for name in names
        ]
        summary_coro = (
            self._guarded("summary", self._load_summary(scope))
            if policy.allows_summary()
            else self._nothing()
        )
        results = await asyncio.gather(*recalls, summary_coro)
        by_layer = dict(zip(names, results[:-1]))
        summary = results[-1] if isinstance(results[-1], str) else ""

        return MemoryBundle(
            question=question,
            window=tuple(
                {"role": str(r.metadata.get("role", "user")), "content": r.content}
                for r in by_layer.get("working") or ()
            ),
            summary=summary,
            facts=tuple(r.content for r in by_layer.get("semantic") or ()),
            episodes=tuple(r.content for r in by_layer.get("episodic") or ()),
            prospects=tuple(
                self._prospect_line(r) for r in by_layer.get("prospective") or ()
            ),
        )

    @staticmethod
    async def _nothing() -> str:
        return ""

    @staticmethod
    def _prospect_line(record: MemoryRecord) -> str:
        due = str(record.metadata.get("due_at") or "")
        return f"{record.content} (due {due[:10]})" if due else record.content

    async def _question_vector(
        self, question: str, allowed: list[str]
    ) -> tuple[float, ...] | None:
        needs_vector = any(
            name in allowed for name in ("semantic", "episodic")
        ) or ("working" in allowed and self._settings.window_semantic_top_k > 0)
        if not needs_vector:
            return None
        try:
            # Lazy: the knowledge package transitively imports the chat
            # package — importing it at module scope would cycle.
            from ...services.knowledge.embedding_service import get_embedding_service

            vectors = await get_embedding_service().embed([question])
            return tuple(vectors[0]) if vectors else None
        except Exception:  # noqa: BLE001 — no embeddings = recency-only memory
            logger.warning("Question embedding unavailable — vector recall skipped")
            return None

    async def _guarded(self, name: str, coro):
        """Per-layer deadline + failure isolation: late or broken layers
        contribute nothing this turn (empty list / empty string)."""
        try:
            return await asyncio.wait_for(coro, timeout=self._settings.timeout_seconds(name))
        except asyncio.TimeoutError:
            logger.warning("Memory layer timed out", extra={"layer": name})
        except Exception:  # noqa: BLE001
            logger.warning("Memory layer failed", extra={"layer": name}, exc_info=True)
        return [] if name != "summary" else ""

    # -- write side (background) ------------------------------------------- #

    def commit_background(self, scope: MemoryScope, *, question: str, answer: str) -> None:
        """Fire-and-forget commit with a held reference (bare create_task
        results are GC-collectable mid-flight)."""
        task = asyncio.create_task(self.commit(scope, question=question, answer=answer))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def commit(self, scope: MemoryScope, *, question: str, answer: str) -> None:
        # §8.3: the policy decides which layers this scope may WRITE to.
        # With the external default (working-only) every branch below is a
        # no-op — the mechanism exists, the policy holds the gate.
        policy = MemoryPolicy.for_scope(scope)
        if policy.allows_summary():
            try:
                await self._roll_summary(scope, question=question, answer=answer)
            except Exception:  # noqa: BLE001 — background: log, never raise
                logger.error("Summary commit failed", exc_info=True)
        try:
            await self._store_extraction(scope, policy, question=question, answer=answer)
        except Exception:  # noqa: BLE001
            logger.error("Extraction commit failed", exc_info=True)
        if policy.allows_layer("episodic"):
            try:
                await self._maybe_write_episode(scope)
            except Exception:  # noqa: BLE001
                logger.error("Episode commit failed", exc_info=True)

    async def _roll_summary(self, scope: MemoryScope, *, question: str, answer: str) -> None:
        previous = await self._load_summary(scope)
        updated = await self._summarizer.update(
            previous=previous,
            turns=[
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
        )
        if not updated or updated == previous:
            return
        # Redact-before-store applies to the summary like any memory write;
        # a credential hit keeps the previous summary rather than storing.
        result = self._redactor.redact(updated)
        if result.dropped:
            return
        await self._store_summary(scope, result.text)

    async def _store_extraction(
        self, scope: MemoryScope, policy: MemoryPolicy, *, question: str, answer: str
    ) -> None:
        """One extractor call feeds both long-term write paths: facts to the
        semantic layer (policy-filtered — external allowlist, §8.3 minimal
        collection) and prospects to the prospective layer."""
        semantic = self._layers.get("semantic") if policy.allows_layer("semantic") else None
        prospective = (
            self._layers.get("prospective") if policy.allows_layer("prospective") else None
        )
        if semantic is None and prospective is None:
            return
        extraction = await self._extractor.extract_turn(question=question, answer=answer)
        created = datetime.now(timezone.utc)
        if semantic is not None:
            facts = policy.filter_facts(list(extraction.facts))
            if facts:
                await semantic.record(
                    scope,
                    [
                        MemoryRecord(
                            layer="semantic", content=fact, weight=1.0,
                            created_at=created, source="extractor",
                        )
                        for fact in facts
                    ],
                )
        if prospective is not None and extraction.prospects:
            await prospective.record(
                scope,
                [
                    MemoryRecord(
                        layer="prospective",
                        content=str(item.get("content") or ""),
                        weight=1.0,
                        created_at=created,
                        source="extractor",
                        metadata={"due_at": str(item.get("due_at") or "")},
                    )
                    for item in extraction.prospects
                ],
            )

    async def _maybe_write_episode(self, scope: MemoryScope) -> None:
        """One episode from the rolling summary when the session's message
        count crosses episodic_min_turns, then one every
        episodic_every_turns thereafter. The modulo tolerates the per-
        channel parity of counts (web commits after both turns landed, SMS
        before the outbound is recorded), so each cycle fires once."""
        layer = self._layers.get("episodic")
        if layer is None:
            return
        min_turns = max(self._settings.episodic_min_turns, 1)
        every = max(self._settings.episodic_every_turns, 1)
        try:
            async with self._db.session() as session:
                count = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(ChatMessageEntity)
                            .where(
                                ChatMessageEntity.session_id == scope.session_id,
                                ChatMessageEntity.tenant_id == scope.tenant_id,
                            )
                        )
                    ).scalar_one()
                )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if count < min_turns or (count - min_turns) % every > 1:
            return
        summary = await self._load_summary(scope)
        if not summary:
            return
        # The episodic layer applies the full §8 write path (redact ->
        # embed from redacted text -> encrypt).
        await layer.record(
            scope,
            [
                MemoryRecord(
                    layer="episodic", content=summary, weight=1.0,
                    created_at=datetime.now(timezone.utc), source="summarizer",
                )
            ],
        )

    # -- session summary store --------------------------------------------- #

    async def _load_summary(self, scope: MemoryScope) -> str:
        try:
            async with self._db.session() as session:
                row = (
                    await session.exec(
                        select(SessionSummaryEntity).where(
                            SessionSummaryEntity.session_id == scope.session_id,
                            SessionSummaryEntity.tenant_id == scope.tenant_id,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if row is None:
            return ""
        try:
            return self._encryptor.decrypt(row.summary)
        except Exception:  # noqa: BLE001 — unreadable summary = no summary
            logger.warning("Session summary failed to decrypt — treated as absent")
            return ""

    async def _store_summary(self, scope: MemoryScope, summary: str) -> None:
        encrypted = self._encryptor.encrypt(summary)
        try:
            async with self._db.session() as session:
                row = (
                    await session.exec(
                        select(SessionSummaryEntity).where(
                            SessionSummaryEntity.session_id == scope.session_id,
                            SessionSummaryEntity.tenant_id == scope.tenant_id,
                        )
                    )
                ).first()
                if row is None:
                    row = SessionSummaryEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=scope.tenant_id,
                        session_id=scope.session_id,
                        summary=encrypted,
                    )
                else:
                    row.summary = encrypted
                    row.updated_at = datetime.now(timezone.utc)
                session.add(row)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    @property
    def layers(self) -> dict[str, MemoryLayer]:
        return dict(self._layers)


_orchestrator: MemoryOrchestrator | None = None


def get_memory_orchestrator() -> MemoryOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MemoryOrchestrator()
    return _orchestrator
