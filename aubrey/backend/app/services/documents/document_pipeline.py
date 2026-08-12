"""The source-agnostic ingestion pipeline.

Sources (SharePoint, blob — anything) only know two things: how to LIST
file references and how to DOWNLOAD one. Everything else is shared here.

Content is stored once per tenant; who may use it is a grant row:

    download -> expand archives -> sha256 of the RAW bytes
      -> content already exists?  add a grant for this team+agent  ("linked")
         (grant already there?                                     "skipped")
      -> new content?  convert with MarkItDown -> document row + grant
         -> same (source_uri, file_name) with different bytes means the
            source file CHANGED: the old row is superseded and every grant
            moves to the new version                               ("processed")

Hashing raw bytes BEFORE conversion makes re-runs cheap (a duplicate skips
conversion and, later, the embedding spend) and keeps identity stable across
MarkItDown upgrades. The `sink` seam is where the embedding step plugs in
next: it receives the converted text once per NEW document.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import OdtTeamEntity, RegisteredAgentEntity
from ...entity.documents import DocumentEntity, DocumentGrantEntity, DocumentStatus
from ...utils.common.logger import Logger
from ...utils.documents import MarkItDownClient, ZipExtractor, get_markitdown_client
from ...utils.errors import NotFoundError
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
    linked: int
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
        await self._ensure_agent_in_team(tenant_id, team_key, agent_key)
        batch_id = await self._batches.start(
            tenant_id=tenant_id,
            team_key=team_key,
            agent_key=agent_key,
            source_type=source_type,
            batch_name=batch_name,
            document_count=len(files),
            properties=properties,
        )

        processed = linked = skipped = failed = 0
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
                        elif outcome == "linked":
                            linked += 1
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
                batch_id=batch_id,
                processed=processed,
                linked=linked,
                skipped=skipped,
                failed=failed,
            )

        await self._batches.complete(
            batch_id=batch_id,
            processed=processed,
            linked=linked,
            skipped=skipped,
            failed=failed,
        )
        logger.info(
            "Ingestion batch finished",
            extra={
                "batch_id": batch_id,
                "team_key": team_key,
                "agent_key": agent_key,
                "processed": processed,
                "linked": linked,
                "skipped": skipped,
                "failed": failed,
            },
        )
        return PipelineResult(
            batch_id=batch_id,
            processed=processed,
            linked=linked,
            skipped=skipped,
            failed=failed,
        )

    async def _ensure_agent_in_team(
        self, tenant_id: str, team_key: str, agent_key: str
    ) -> None:
        """Grants reference team + agent — refuse to mint ownership rows for
        keys that were never registered."""
        async with self._db.session() as session:
            team = (
                await session.exec(
                    select(OdtTeamEntity).where(
                        OdtTeamEntity.tenant_id == tenant_id,
                        OdtTeamEntity.key == team_key,
                    )
                )
            ).first()
            if team is None:
                raise NotFoundError(
                    f"Team '{team_key}' is not registered.",
                    details={"team_key": team_key},
                )
            agent = (
                await session.exec(
                    select(RegisteredAgentEntity).where(
                        RegisteredAgentEntity.tenant_id == tenant_id,
                        RegisteredAgentEntity.agent_key == agent_key,
                    )
                )
            ).first()
            if agent is None or agent.team_id != team.id:
                raise NotFoundError(
                    f"Agent '{agent_key}' is not registered under team '{team_key}'.",
                    details={"team_key": team_key, "agent_key": agent_key},
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
        sha256 = hashlib.sha256(content).hexdigest()

        async with self._db.session() as session:
            existing = (
                await session.exec(
                    select(DocumentEntity).where(
                        DocumentEntity.tenant_id == tenant_id,
                        DocumentEntity.sha256 == sha256,
                    )
                )
            ).first()
            if existing is not None:
                granted = await self._ensure_grant(
                    session, tenant_id, existing.id, team_key, agent_key
                )
                if granted:
                    logger.info(
                        "Existing content granted to agent (no re-conversion)",
                        extra={"file_name": file_name, "agent_key": agent_key},
                    )
                    return "linked"
                logger.info(
                    "Duplicate document skipped (already granted)",
                    extra={"file_name": file_name, "agent_key": agent_key},
                )
                return "skipped"

        # New content — the only path that pays for conversion.
        try:
            text = await self._markitdown.aconvert_bytes(content, file_name)
        except Exception:  # noqa: BLE001 — unsupported type = counted failure
            logger.warning(
                "Document conversion failed",
                extra={"file_name": file_name, "batch_id": batch_id},
            )
            return "failed"

        document_id = uuid.uuid4().hex
        async with self._db.session() as session:
            await self._supersede_old_version(
                session, tenant_id, source_uri, file_name, document_id
            )
            session.add(
                DocumentEntity(
                    id=document_id,
                    tenant_id=tenant_id,
                    batch_id=batch_id,
                    source_type=source_type,
                    file_name=file_name,
                    source_uri=source_uri or None,
                    sha256=sha256,
                    size_bytes=len(content),
                    doc_metadata={"characters": len(text)},
                )
            )
            await self._ensure_grant(session, tenant_id, document_id, team_key, agent_key)

        if sink is not None:
            await sink(document_id, file_name, text)
        return "processed"

    async def _ensure_grant(
        self,
        session,
        tenant_id: str,
        document_id: str,
        team_key: str,
        agent_key: str,
    ) -> bool:
        """True if a new grant row was created, False if it already existed."""
        grant = (
            await session.exec(
                select(DocumentGrantEntity).where(
                    DocumentGrantEntity.tenant_id == tenant_id,
                    DocumentGrantEntity.document_id == document_id,
                    DocumentGrantEntity.team_key == team_key,
                    DocumentGrantEntity.agent_key == agent_key,
                )
            )
        ).first()
        if grant is not None:
            return False
        session.add(
            DocumentGrantEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                document_id=document_id,
                team_key=team_key,
                agent_key=agent_key,
            )
        )
        return True

    async def _supersede_old_version(
        self,
        session,
        tenant_id: str,
        source_uri: str,
        file_name: str,
        new_document_id: str,
    ) -> None:
        """Same source location, different bytes = the file changed. The old
        row is marked superseded and its grants move to the new version, so
        every agent reading that source sees the update."""
        if not source_uri:
            return
        old = (
            await session.exec(
                select(DocumentEntity).where(
                    DocumentEntity.tenant_id == tenant_id,
                    DocumentEntity.source_uri == source_uri,
                    DocumentEntity.file_name == file_name,
                    DocumentEntity.status != DocumentStatus.SUPERSEDED,
                )
            )
        ).first()
        if old is None:
            return
        old.status = DocumentStatus.SUPERSEDED
        old.updated_at = datetime.now(timezone.utc)
        session.add(old)
        grants = (
            await session.exec(
                select(DocumentGrantEntity).where(
                    DocumentGrantEntity.document_id == old.id
                )
            )
        ).all()
        for grant in grants:
            grant.document_id = new_document_id
            grant.updated_at = datetime.now(timezone.utc)
            session.add(grant)
        logger.info(
            "Document superseded by a new version",
            extra={
                "source_uri": source_uri,
                "file_name": file_name,
                "old_document_id": old.id,
                "grants_moved": len(grants),
            },
        )


_pipeline: DocumentPipeline | None = None


def get_document_pipeline() -> DocumentPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = DocumentPipeline()
    return _pipeline
