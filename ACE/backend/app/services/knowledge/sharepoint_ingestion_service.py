import asyncio
from dataclasses import dataclass

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from ...utils.sharepoint.sharepoint_helpers import SharePointClient
from ..embedding.ingestion_service import IngestionService, get_ingestion_service

logger = Logger(__name__).get_logger()

_SOURCE_PREFIX = "sharepoint:"


@dataclass(frozen=True)
class SharePointIngestionResult:
    knowledge_source: str
    files_ingested: int
    files_skipped: int
    chunk_count: int


class SharePointIngestionService:
    """Phase A of the SharePoint agent: team-driven bulk load into pgvector.

    The team supplies site/drive/folder (their ownership); ACE supplies the
    Graph connection (yaml `microsoft.sharepoint`). Every document lands
    under `sharepoint:<source_name>` — write permission on that source is
    enforced by the ingestion pipeline (Casbin), read permission at query
    time by the KnowledgeGateway.
    """

    def __init__(self, ingestion: IngestionService | None = None) -> None:
        self._ingestion = ingestion or get_ingestion_service()

    @staticmethod
    def _resolve_settings(connection_config: dict | None) -> dict:
        """Team connection config (the standard path) or the legacy yaml
        section — one code path either way, just a different value source."""
        if connection_config:
            return dict(connection_config)
        return get_application_context().microsoft.get("sharepoint", {}) or {}

    def _client(self, connection_config: dict | None = None) -> SharePointClient:
        cfg = self._resolve_settings(connection_config)
        for key in ("tenant_id", "client_id", "client_secret", "hostname"):
            if not PlaceholderPolicy.is_configured(cfg.get(key)):
                hint = (
                    f"connection config key '{key}'"
                    if connection_config
                    else f"microsoft.sharepoint.{key} in the env yaml (or register a sharepoint connection)"
                )
                raise ValidationError(f"SharePoint is not configured. Set {hint}.")
        return SharePointClient(
            tenant_id=cfg["tenant_id"],
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
        )

    async def ingest_folder(
        self,
        *,
        context: SessionContext,
        source_name: str,
        site_path: str,
        drive_name: str,
        folder_path: str = "",
        chunking_strategy: str | None = None,
        connection_config: dict | None = None,
    ) -> SharePointIngestionResult:
        normalized = (source_name or "").strip().lower()
        if not normalized:
            raise ValidationError("source_name is required.")
        knowledge_source = f"{_SOURCE_PREFIX}{normalized}"

        files = await asyncio.to_thread(
            self._list_files, site_path, drive_name, folder_path, connection_config
        )
        ingested = skipped = chunks = 0
        for filename, raw_bytes in files:
            try:
                result = await self._ingestion.ingest_file(
                    context=context,
                    knowledge_source=knowledge_source,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    source_type="sharepoint",
                    chunking_strategy=chunking_strategy,
                )
                chunks += result.chunk_count
                if result.status == "skipped":
                    skipped += 1
                else:
                    ingested += 1
            except Exception:  # noqa: BLE001 — one bad file must not sink the batch
                logger.error(
                    "SharePoint file ingestion failed; continuing",
                    extra={"filename": filename, "knowledge_source": knowledge_source},
                    exc_info=True,
                )
                skipped += 1

        logger.info(
            "SharePoint folder ingested",
            extra={
                "knowledge_source": knowledge_source,
                "ingested": ingested,
                "skipped": skipped,
            },
        )
        return SharePointIngestionResult(
            knowledge_source=knowledge_source,
            files_ingested=ingested,
            files_skipped=skipped,
            chunk_count=chunks,
        )

    def _list_files(
        self,
        site_path: str,
        drive_name: str,
        folder_path: str,
        connection_config: dict | None = None,
    ) -> list[tuple[str, bytes]]:
        cfg = self._resolve_settings(connection_config)
        client = self._client(connection_config)
        try:
            site_id = client.get_site_id(cfg["hostname"], site_path)
            drive = client.get_drive_by_name(site_id, drive_name)
            drive_id = drive["id"]
            folder_id = (
                client.resolve_folder_id_by_path(drive_id, folder_path)
                if folder_path
                else "root"
            )
            files: list[tuple[str, bytes]] = []
            for item in client.list_items_recursive(drive_id, folder_id):
                if item.get("file") is None:
                    continue
                content = client.download_file_content(drive_id, item["id"])
                files.append((item.get("name", "document"), content))
            return files
        except (ValidationError, ExternalServiceError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "SharePoint listing/download failed — check microsoft.sharepoint "
                "settings and the team's site/drive/path.",
                code="sharepoint_ingest_failed",
                cause=exc,
            ) from exc


_service: SharePointIngestionService | None = None


def get_sharepoint_ingestion_service() -> SharePointIngestionService:
    global _service
    if _service is None:
        _service = SharePointIngestionService()
    return _service
