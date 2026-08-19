"""Shared machinery for the pgvector-backed long-term layers (semantic
facts, episodic summaries) — one implementation of the §8 write path so no
subclass can accidentally skip a step:

    record  = redact -> embed(REDACTED plaintext) -> encrypt -> insert
    recall  = cosine top-k on (tenant, user) -> decrypt -> MemoryRecord
    decay   = weight recomputed from age (0.5 ** (age_days / half_life)),
              prune below the floor

Decay recomputes the ABSOLUTE weight from created_at rather than
compounding a multiplier, so the job is idempotent — a retried or
double-scheduled run (multiple workers) cannot over-decay. That holds
while every record is born at weight 1.0; feedback boosting (M10d) will
need a stored base weight. Rows whose key can't decrypt are skipped with
a warning, never fatal. No question vector (embedding endpoint absent)
means recall degrades to empty."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import text as sql_text

from ....database.rdbms.pg_session import get_postgres_connector
from ....utils.common.logger import Logger
from ....utils.crypto import FieldEncryptor, get_field_encryptor
from ....utils.errors import DatabaseError
from ..layer import MemoryLayer, RecallQuery
from ..record import MemoryRecord
from ..redactor import MemoryRedactor, get_memory_redactor
from ..scope import MemoryScope
from ..settings import MemorySettings, get_memory_settings

logger = Logger(__name__).get_logger()


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


class PgVectorMemoryLayer(MemoryLayer):
    # Subclasses set these; _table is a static identifier, never user input.
    name: str = ""
    _table: str = ""
    _half_life_key: str = ""

    def __init__(
        self,
        settings: MemorySettings | None = None,
        encryptor: FieldEncryptor | None = None,
        redactor: MemoryRedactor | None = None,
    ) -> None:
        self._settings = settings or get_memory_settings()
        self._encryptor = encryptor or get_field_encryptor()
        self._redactor = redactor or get_memory_redactor()
        self._db = get_postgres_connector()

    # -- subclass hooks --------------------------------------------------- #

    def _top_k(self) -> int:
        raise NotImplementedError

    def _make_entity(
        self, scope: MemoryScope, *, content: str, embedding: list[float],
        weight: float, source: str,
    ):
        raise NotImplementedError

    # -- MemoryLayer ------------------------------------------------------ #

    async def recall(
        self, scope: MemoryScope, query: RecallQuery, budget_tokens: int
    ) -> list[MemoryRecord]:
        if not query.vector:
            return []
        statement = sql_text(
            f"""
            SELECT content, weight, source, created_at
            FROM {self._table}
            WHERE tenant_id = :tenant_id AND user_id = :user_id
              AND embedding IS NOT NULL AND weight >= :floor
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :top_k
            """
        )
        try:
            async with self._db.session() as session:
                rows = (
                    await session.execute(
                        statement,
                        {
                            "tenant_id": scope.tenant_id,
                            "user_id": scope.user_id,
                            "floor": self._settings.decay_floor,
                            "vec": _vector_literal(list(query.vector)),
                            "top_k": self._top_k(),
                        },
                    )
                ).mappings().all()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        records: list[MemoryRecord] = []
        for row in rows:
            try:
                content = self._encryptor.decrypt(row["content"])
            except Exception:  # noqa: BLE001 — one bad row never sinks recall
                logger.warning(
                    "Memory row failed to decrypt — skipped",
                    extra={"table": self._table},
                )
                continue
            records.append(
                MemoryRecord(
                    layer=self.name, content=content,
                    weight=float(row["weight"] or 0.0),
                    created_at=row["created_at"],
                    source=str(row["source"] or ""),
                )
            )
        return records

    async def record(self, scope: MemoryScope, records: list[MemoryRecord]) -> None:
        cleared: list[tuple[str, MemoryRecord]] = []
        for item in records:
            result = self._redactor.redact(item.content)
            if result.dropped or not result.text.strip():
                continue  # credential drops already alerted inside the redactor
            cleared.append((result.text, item))
        if not cleared:
            return
        # Embed the REDACTED plaintext (vectors must never encode raw
        # identifiers), then encrypt for the at-rest column. Lazy import:
        # the knowledge package transitively imports the chat package.
        from ....services.knowledge.embedding_service import get_embedding_service

        vectors = await get_embedding_service().embed([text for text, _ in cleared])
        try:
            async with self._db.session() as session:
                for (content, item), vector in zip(cleared, vectors):
                    session.add(
                        self._make_entity(
                            scope,
                            content=self._encryptor.encrypt(content),
                            embedding=vector,
                            weight=1.0,
                            source=item.source,
                        )
                    )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def decay(self) -> int:
        half_life = float(self._settings.half_life_days.get(self._half_life_key, 90))
        try:
            async with self._db.session() as session:
                await session.execute(
                    sql_text(
                        f"""
                        UPDATE {self._table}
                        SET weight = power(
                            0.5,
                            EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 / :half_life
                        )
                        """
                    ),
                    {"half_life": half_life},
                )
                pruned = await session.execute(
                    sql_text(f"DELETE FROM {self._table} WHERE weight < :floor"),
                    {"floor": self._settings.decay_floor},
                )
                return int(pruned.rowcount or 0)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex
