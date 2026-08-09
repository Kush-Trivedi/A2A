import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents.ingestion_job_entity import IngestionJobEntity, IngestionJobStatus
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import AppError, DatabaseError, NotFoundError

logger = Logger(__name__).get_logger()


class IngestionJobService:
    """Source-agnostic background jobs for bulk ingestion.

    A long load (SharePoint folder, blob container, anything future) runs off
    the request path: the route returns a job id immediately, the work runs
    as an asyncio task, and the job row tracks the outcome. The same shape a
    Service Bus worker consumes later — swapping the runner does not change
    the API.
    """

    def __init__(self) -> None:
        self._connector = get_postgres_connector()

    async def start(
        self,
        *,
        context: SessionContext,
        kind: str,
        source_name: str,
        work: Callable[[], Awaitable[dict[str, Any]]],
    ) -> str:
        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        entity = IngestionJobEntity(
            id=job_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            kind=kind,
            source_name=source_name,
            status=IngestionJobStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._connector.session() as session:
                session.add(entity)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        asyncio.create_task(self._run(job_id, kind, work))
        logger.info(
            "Ingestion job started",
            extra={"job_id": job_id, "kind": kind, "source_name": source_name},
        )
        return job_id

    async def _run(
        self, job_id: str, kind: str, work: Callable[[], Awaitable[dict[str, Any]]]
    ) -> None:
        try:
            detail = await work()
            await self._finish(job_id, IngestionJobStatus.COMPLETED, detail)
        except AppError as exc:
            await self._finish(
                job_id,
                IngestionJobStatus.FAILED,
                {"error": exc.client_message(), "code": exc.code},
            )
        except Exception as exc:  # noqa: BLE001 — job failures must be recorded
            logger.error(
                "Ingestion job crashed", extra={"job_id": job_id, "kind": kind}, exc_info=True
            )
            await self._finish(
                job_id, IngestionJobStatus.FAILED, {"error": "Ingestion failed unexpectedly."}
            )

    async def _finish(self, job_id: str, status: str, detail: dict[str, Any]) -> None:
        try:
            async with self._connector.session() as session:
                job = (
                    await session.exec(
                        select(IngestionJobEntity).where(IngestionJobEntity.id == job_id)
                    )
                ).one()
                job.status = status
                job.detail = detail
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
        except Exception:  # noqa: BLE001
            logger.error("Ingestion job status write failed", extra={"job_id": job_id}, exc_info=True)

    async def get(self, *, context: SessionContext, job_id: str) -> IngestionJobEntity:
        try:
            async with self._connector.session() as session:
                job = (
                    await session.exec(
                        select(IngestionJobEntity).where(
                            IngestionJobEntity.id == job_id,
                            IngestionJobEntity.tenant_id == context.tenant_id,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if job is None:
            raise NotFoundError(f"Ingestion job '{job_id}' not found.")
        return job


_service: IngestionJobService | None = None


def get_ingestion_job_service() -> IngestionJobService:
    global _service
    if _service is None:
        _service = IngestionJobService()
    return _service
