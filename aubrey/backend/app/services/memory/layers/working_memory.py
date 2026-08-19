"""Working memory — the current session's turns, upgraded from pure
recency to hybrid recency+relevance selection over the EXISTING
chat_messages rows (no schema change, nothing stored here).

Selection: the last N turns always ride (conversation flow must survive),
and when the session has scrolled past them, the top-K OLDER turns by
embedding cosine against the question are pulled back in — so "what did we
decide about X" recovers the X turn even 40 messages later. Older-turn
vectors are computed in Python via the shared embedding service and cached
in-process by message id (messages are immutable; edits produce new
content but keep the id — the cache keys on a content hash for that
reason). The token budget is enforced by the same MemoryWindowBuilder the
envelope has always used; with no question vector (local dev, embedding
absent) selection degrades to plain recency."""

import hashlib
from collections import OrderedDict
from datetime import datetime, timezone

from sqlmodel import select

from ....database.rdbms.pg_session import get_postgres_connector
from ....entity.chat import ChatMessageEntity
from ....utils.common.logger import Logger
from ....utils.crypto import decrypt_or_keep, get_field_encryptor
from ....utils.errors import DatabaseError
from ..layer import MemoryLayer, RecallQuery
from ..record import MemoryRecord
from ..scope import MemoryScope
from ..settings import MemorySettings, get_memory_settings
from ...chat.memory_window import MemoryWindowBuilder, get_memory_window_builder

logger = Logger(__name__).get_logger()

_EMBED_CACHE_MAX = 4096


class WorkingMemoryLayer(MemoryLayer):
    name = "working"

    def __init__(
        self,
        settings: MemorySettings | None = None,
        windows: MemoryWindowBuilder | None = None,
    ) -> None:
        self._settings = settings or get_memory_settings()
        self._windows = windows or get_memory_window_builder()
        self._db = get_postgres_connector()
        self._vector_cache: OrderedDict[str, list[float]] = OrderedDict()

    async def recall(
        self, scope: MemoryScope, query: RecallQuery, budget_tokens: int
    ) -> list[MemoryRecord]:
        messages = await self._load_messages(scope)
        eligible = [m for m in messages if MemoryWindowBuilder._belongs_in_window(m)]
        recent_n = self._settings.window_recent_turns
        recent = eligible[-recent_n:] if recent_n > 0 else []
        older = eligible[: len(eligible) - len(recent)]

        selected = recent
        if older and query.vector and self._settings.window_semantic_top_k > 0:
            picked = await self._semantic_pick(older, list(query.vector))
            keep = {id(m) for m in picked} | {id(m) for m in recent}
            selected = [m for m in eligible if id(m) in keep]  # chronological

        window = self._windows.build(selected, window_tokens=budget_tokens)
        now = datetime.now(timezone.utc)
        return [
            MemoryRecord(
                layer=self.name, content=w.content, weight=1.0,
                created_at=now, source="session", metadata={"role": w.role},
            )
            for w in window
        ]

    async def record(self, scope: MemoryScope, records: list[MemoryRecord]) -> None:
        """No-op by contract: turns are persisted by the session service —
        working memory is a VIEW over chat_messages, never a second store."""

    async def decay(self) -> int:
        return 0  # session rows have their own retention story (M10-S2)

    async def _semantic_pick(
        self, older: list[ChatMessageEntity], question_vector: list[float]
    ) -> list[ChatMessageEntity]:
        try:
            vectors = await self._vectors_for(older)
        except Exception:  # noqa: BLE001 — relevance is an upgrade, recency the floor
            logger.warning("Older-turn embedding failed — recency-only window", exc_info=True)
            return []
        scored = [
            (self._cosine(question_vector, vec), message)
            for message, vec in zip(older, vectors)
            if vec is not None
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [message for _, message in scored[: self._settings.window_semantic_top_k]]

    async def _vectors_for(
        self, messages: list[ChatMessageEntity]
    ) -> list[list[float] | None]:
        keys = [
            f"{m.id}:{hashlib.sha256(m.content.encode('utf-8')).hexdigest()[:16]}"
            for m in messages
        ]
        missing = [
            (i, messages[i].content)
            for i, key in enumerate(keys)
            if key not in self._vector_cache
        ]
        if missing:
            # Lazy: the knowledge package transitively imports the chat
            # package — importing it at module scope would cycle.
            from ....services.knowledge.embedding_service import get_embedding_service

            fresh = await get_embedding_service().embed([text for _, text in missing])
            for (i, _), vector in zip(missing, fresh):
                self._vector_cache[keys[i]] = vector
                while len(self._vector_cache) > _EMBED_CACHE_MAX:
                    self._vector_cache.popitem(last=False)
        return [self._vector_cache.get(key) for key in keys]

    async def _load_messages(self, scope: MemoryScope) -> list[ChatMessageEntity]:
        # Same query shape as ChatSessionService._messages — scoped by
        # (tenant, session); ownership was enforced by the calling surface
        # before the scope was ever constructed.
        try:
            async with self._db.session() as session:
                rows = list(
                    (
                        await session.exec(
                            select(ChatMessageEntity)
                            .where(
                                ChatMessageEntity.session_id == scope.session_id,
                                ChatMessageEntity.tenant_id == scope.tenant_id,
                            )
                            .order_by(ChatMessageEntity.created_at)  # type: ignore[arg-type]
                        )
                    ).all()
                )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        # M10-S1: chat_messages.content is encrypted at rest — decrypt on
        # detached rows (never inside the session, so plaintext can't flush
        # back). Legacy plaintext rows pass through unchanged.
        encryptor = get_field_encryptor()
        for row in rows:
            row.content = decrypt_or_keep(encryptor, row.content)
        return rows

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
