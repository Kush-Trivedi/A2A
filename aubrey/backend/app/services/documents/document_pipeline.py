"""The source-agnostic ingestion pipeline.

Sources (SharePoint, blob — anything) only know two things: how to LIST
file references and how to DOWNLOAD one. Everything else is shared here:

    download -> expand archives -> convert (MarkItDown, any supported type)
    -> sha256 dedup within the owner scope -> document row -> live counts

Every run is one batch; every document belongs to the batch's team + agent.
The `sink` seam is where the embedding step plugs in next: it receives the
converted text per document and will chunk + embed + upsert to pgvector.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.documents import DocumentEntity
from ...utils.common.logger import Logger
from ...utils.documents import MarkItDownClient, ZipExtractor, get_markitdown_client
from .batch_tracker import BatchTracker, get_batch_tracker

logger = Logger(__name__).get_logger()

# sink(document_id, file_name, text) — the embedding step, when it arrives.
DocumentSink = Callable[[str, str, str], Awaitable[None]]


@dataclass(frozen=True)
class SourceFile:
    """A file reference BEFORE download — bytes are fetched one file at a
    time, never all up-front."""

    name: str
    uri: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineResult:
    batch_id: str
    processed: int
    skipped: int
    failed: int


class DocumentPipeline:
    _BATCH_SIZE = 10

    def __init__(
        self,
        markitdown: MarkItDownClient | None = None,
        batches: BatchTracker | None = None,
        zips: ZipExtractor | None = None,
    ) -> None:
        self._markitdown = markitdown or get_markitdown_client()
        self._batches = batches or get_batch_tracker()
        self._zips = zips or ZipExtractor()
        self._db = get_postgres_connector()

    async def run(
        self,
        *,
        tenant_id: str,
        team_key: str,
        agent_key: str,
        source_type: str,
        batch_name: str,
        files: list[SourceFile],
        download: Callable[[SourceFile], Awaitable[bytes]],
        properties: dict | None = None,
        sink: DocumentSink | None = None,
    ) -> PipelineResult:
        batch_id = await self._batches.start(
            tenant_id=tenant_id,
            team_key=team_key,
            agent_key=agent_key,
            source_type=source_type,
            batch_name=batch_name,
            document_count=len(files),
            properties=properties,
        )

        processed = skipped = failed = 0
        for start in range(0, len(files), self._BATCH_SIZE):
            for source_file in files[start : start + self._BATCH_SIZE]:
                try:
                    raw_bytes = await download(source_file)
                    for name, content in self._expand(source_file.name, raw_bytes):
                        outcome = await self._process_one(
                            tenant_id=tenant_id,
                            team_key=team_key,
                            agent_key=agent_key,
                            source_type=source_type,
                            batch_id=batch_id,
                            file_name=name,
                            source_uri=source_file.uri,
                            content=content,
                            sink=sink,
                        )
                        if outcome == "processed":
                            processed += 1
                        elif outcome == "skipped":
                            skipped += 1
                        else:
                            failed += 1
                except Exception:  # noqa: BLE001 — one bad file never sinks the run
                    logger.error(
                        "Document ingestion failed; continuing",
                        extra={"file_name": source_file.name, "batch_id": batch_id},
                        exc_info=True,
                    )
                    failed += 1
            await self._batches.record_progress(
                batch_id=batch_id, processed=processed, skipped=skipped, failed=failed
            )

        await self._batches.complete(
            batch_id=batch_id, processed=processed, skipped=skipped, failed=failed
        )
        logger.info(
            "Ingestion batch finished",
            extra={
                "batch_id": batch_id,
                "team_key": team_key,
                "agent_key": agent_key,
                "processed": processed,
                "skipped": skipped,
                "failed": failed,
            },
        )
        return PipelineResult(
            batch_id=batch_id, processed=processed, skipped=skipped, failed=failed
        )

    def _expand(self, name: str, raw_bytes: bytes) -> list[tuple[str, bytes]]:
        """A zip becomes its inner files (nested folders and archives
        included); anything else passes through unchanged."""
        if ZipExtractor.is_zip(name, raw_bytes):
            return self._zips.extract(raw_bytes, name)
        return [(name, raw_bytes)]

    async def _process_one(
        self,
        *,
        tenant_id: str,
        team_key: str,
        agent_key: str,
        source_type: str,
        batch_id: str,
        file_name: str,
        source_uri: str,
        content: bytes,
        sink: DocumentSink | None,
    ) -> str:
        try:
            text = await self._markitdown.aconvert_bytes(content, file_name)
        except Exception:  # noqa: BLE001 — unsupported type = counted failure
            logger.warning(
                "Document conversion failed",
                extra={"file_name": file_name, "batch_id": batch_id},
            )
            return "failed"

        sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if await self._duplicate_exists(tenant_id, agent_key, sha256):
            logger.info(
                "Duplicate document skipped (content hash match)",
                extra={"file_name": file_name, "agent_key": agent_key},
            )
            return "skipped"

        document_id = uuid.uuid4().hex
        async with self._db.session() as session:
            session.add(
                DocumentEntity(
                    id=document_id,
                    tenant_id=tenant_id,
                    team_key=team_key,
                    agent_key=agent_key,
                    batch_id=batch_id,
                    source_type=source_type,
                    file_name=file_name,
                    source_uri=source_uri or None,
                    sha256=sha256,
                    size_bytes=len(content),
                    doc_metadata={"characters": len(text)},
                )
            )

        if sink is not None:
            await sink(document_id, file_name, text)
        return "processed"

    async def _duplicate_exists(
        self, tenant_id: str, agent_key: str, sha256: str
    ) -> bool:
        async with self._db.session() as session:
            existing = (
                await session.exec(
                    select(DocumentEntity.id).where(
                        DocumentEntity.tenant_id == tenant_id,
                        DocumentEntity.agent_key == agent_key,
                        DocumentEntity.sha256 == sha256,
                    )
                )
            ).first()
            return existing is not None


_pipeline: DocumentPipeline | None = None


def get_document_pipeline() -> DocumentPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DocumentPipeline()
    return _pipeline
