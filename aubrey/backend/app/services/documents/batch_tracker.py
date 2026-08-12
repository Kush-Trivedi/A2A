import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.documents import BatchStatus, DocumentBatchEntity
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError

logger = Logger(__name__).get_logger()


class BatchTracker:
    """One row per ingestion run in `document_batches`, owned by a team +
    agent, with live counts as the run progresses."""

    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def start(
        self,
        *,
        tenant_id: str,
        team_key: str,
        agent_key: str,
        source_type: str,
        batch_name: str,
        document_count: int,
        properties: dict | None = None,
    ) -> str:
        batch_id = uuid.uuid4().hex
        try:
            async with self._db.session() as session:
                session.add(
                    DocumentBatchEntity(
                        id=batch_id,
                        tenant_id=tenant_id,
                        team_key=team_key,
                        agent_key=agent_key,
                        source_type=source_type,
                        batch_name=batch_name,
                        document_count=document_count,
                        properties=dict(properties or {}),
                    )
                )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        return batch_id

    async def record_progress(
        self, *, batch_id: str, processed: int, linked: int, skipped: int, failed: int
    ) -> None:
        await self._update(batch_id, processed, linked, skipped, failed, status=None)

    async def complete(
        self, *, batch_id: str, processed: int, linked: int, skipped: int, failed: int
    ) -> None:
        status = BatchStatus.COMPLETED_WITH_ERRORS if failed else BatchStatus.COMPLETED
        await self._update(batch_id, processed, linked, skipped, failed, status=status)

    async def _update(
        self,
        batch_id: str,
        processed: int,
        linked: int,
        skipped: int,
        failed: int,
        status: str | None,
    ) -> None:
        try:
            async with self._db.session() as session:
                batch = (
                    await session.exec(
                        select(DocumentBatchEntity).where(
                            DocumentBatchEntity.id == batch_id
                        )
                    )
                ).one()
                batch.processed_count = processed
                batch.linked_count = linked
                batch.skipped_count = skipped
                batch.failed_count = failed
                if status is not None:
                    batch.status = status
                batch.updated_at = datetime.now(timezone.utc)
                session.add(batch)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_tracker: BatchTracker | None = None


def get_batch_tracker() -> BatchTracker:
    global _tracker
    if _tracker is None:
        _tracker = BatchTracker()
    return _tracker
