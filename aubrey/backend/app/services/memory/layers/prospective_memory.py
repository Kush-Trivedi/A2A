"""Prospective memory — future commitments ("remind me", "follow up after
results", campaign follow-ups), recalled by DUE WINDOW rather than
similarity: a session near (or past) a commitment's due date surfaces it,
whatever the current question is (NEW_PLAN §2 row 7, M10c).

Write path is the §8 discipline minus the embedding (no vector — recall
never ranks by similarity): redact -> encrypt -> insert, status open.
Decay is the append+decay rule expressed as state, not weights: open
prospects further past due than the yaml horizon (prospect_stale_days)
flip to cancelled — rows are never rewritten or deleted by decay, so the
commitment trail stays auditable. Rows whose key can't decrypt are
skipped with a warning, never fatal."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text

from ....database.rdbms.pg_session import get_postgres_connector
from ....entity.memory import MemoryProspectEntity, ProspectStatus
from ....utils.common.logger import Logger
from ....utils.crypto import FieldEncryptor, get_field_encryptor
from ....utils.errors import DatabaseError
from ..layer import MemoryLayer, RecallQuery
from ..record import MemoryRecord
from ..redactor import MemoryRedactor, get_memory_redactor
from ..scope import MemoryScope
from ..settings import MemorySettings, get_memory_settings

logger = Logger(__name__).get_logger()


class ProspectiveMemoryLayer(MemoryLayer):
    name = "prospective"

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

    async def recall(
        self, scope: MemoryScope, query: RecallQuery, budget_tokens: int
    ) -> list[MemoryRecord]:
        horizon = datetime.now(timezone.utc) + timedelta(
            days=self._settings.prospect_horizon_days
        )
        statement = sql_text(
            """
            SELECT content, due_at, source_session, created_at
            FROM memory_prospects
            WHERE tenant_id = :tenant_id AND user_id = :user_id
              AND status = :open
              AND (due_at IS NULL OR due_at <= :horizon)
            ORDER BY due_at ASC NULLS LAST, created_at ASC
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
                            "open": ProspectStatus.OPEN,
                            "horizon": horizon,
                            "top_k": self._settings.prospects_top_k,
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
                logger.warning("Prospect row failed to decrypt — skipped")
                continue
            due_at = row["due_at"]
            records.append(
                MemoryRecord(
                    layer=self.name,
                    content=content,
                    weight=1.0,
                    created_at=row["created_at"],
                    source="extractor",
                    metadata={
                        "due_at": due_at.isoformat() if due_at else "",
                        "source_session": str(row["source_session"] or ""),
                    },
                )
            )
        return records

    async def record(self, scope: MemoryScope, records: list[MemoryRecord]) -> None:
        entities: list[MemoryProspectEntity] = []
        for item in records:
            result = self._redactor.redact(item.content)
            if result.dropped or not result.text.strip():
                continue  # credential drops already alerted inside the redactor
            entities.append(
                MemoryProspectEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    content=self._encryptor.encrypt(result.text),
                    due_at=self._parse_due(item.metadata.get("due_at")),
                    status=ProspectStatus.OPEN,
                    source_session=scope.session_id,
                )
            )
        if not entities:
            return
        try:
            async with self._db.session() as session:
                for entity in entities:
                    session.add(entity)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def decay(self) -> int:
        """Cancel open prospects stale beyond the yaml horizon. Undated
        prospects age from created_at. Idempotent — cancelled stays
        cancelled."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._settings.prospect_stale_days
        )
        try:
            async with self._db.session() as session:
                result = await session.execute(
                    sql_text(
                        """
                        UPDATE memory_prospects
                        SET status = :cancelled
                        WHERE status = :open
                          AND (
                            (due_at IS NOT NULL AND due_at < :cutoff)
                            OR (due_at IS NULL AND created_at < :cutoff)
                          )
                        """
                    ),
                    {
                        "cancelled": ProspectStatus.CANCELLED,
                        "open": ProspectStatus.OPEN,
                        "cutoff": cutoff,
                    },
                )
                return int(result.rowcount or 0)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    @staticmethod
    def _parse_due(raw) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed


_layer: ProspectiveMemoryLayer | None = None


def get_prospective_memory_layer() -> ProspectiveMemoryLayer:
    global _layer
    if _layer is None:
        _layer = ProspectiveMemoryLayer()
    return _layer
