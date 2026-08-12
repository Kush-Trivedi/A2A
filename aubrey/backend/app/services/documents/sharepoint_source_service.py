"""SharePoint source: list a drive folder (recursively) or one named file,
download per file, and hand everything to the shared DocumentPipeline.
Credentials are platform-held (microsoft.sharepoint in the env yaml)."""

import asyncio

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from ...utils.sharepoint.sharepoint_helpers import SharePointClient
from .document_pipeline import (
    DocumentPipeline,
    DocumentSink,
    PipelineResult,
    SourceFile,
    get_document_pipeline,
)

logger = Logger(__name__).get_logger()


class SharePointSourceService:
    def __init__(self, pipeline: DocumentPipeline | None = None) -> None:
        self._pipeline = pipeline or get_document_pipeline()

    def _settings(self) -> dict:
        cfg = get_application_context().microsoft.get("sharepoint", {}) or {}
        for key in ("tenant_id", "client_id", "client_secret", "hostname"):
            if not PlaceholderPolicy.is_configured(cfg.get(key)):
                raise ValidationError(
                    f"SharePoint is not configured. Set microsoft.sharepoint.{key} in the env yaml."
                )
        return cfg

    def _client(self) -> SharePointClient:
        cfg = self._settings()
        return SharePointClient(
            tenant_id=cfg["tenant_id"],
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
        )

    async def ingest(
        self,
        *,
        context: SessionContext,
        team_key: str,
        agent_key: str,
        site_path: str,
        drive_name: str,
        folder_path: str = "",
        file_name: str | None = None,
        sink: DocumentSink | None = None,
    ) -> PipelineResult:
        files = await asyncio.to_thread(
            self._list_files, site_path, drive_name, folder_path
        )
        if file_name and file_name.strip():
            wanted = file_name.strip().lower()
            files = [f for f in files if f.name.rsplit("/", 1)[-1].lower() == wanted]
            if not files:
                raise ValidationError(
                    f"File '{file_name}' was not found in the given site/drive/folder.",
                    details={"file_name": file_name},
                )

        return await self._pipeline.run(
            tenant_id=context.tenant_id,
            team_key=team_key.strip().lower(),
            agent_key=agent_key.strip().lower(),
            source_type="sharepoint",
            batch_name=f"sharepoint:{site_path}/{drive_name}/{folder_path or ''}".rstrip("/"),
            files=files,
            download=self._downloader(),
            properties={
                "site_path": site_path,
                "drive_name": drive_name,
                "folder_path": folder_path,
                "file_name": file_name or "",
            },
            sink=sink,
        )

    def _list_files(
        self, site_path: str, drive_name: str, folder_path: str
    ) -> list[SourceFile]:
        cfg = self._settings()
        client = self._client()
        try:
            site_id = client.get_site_id(cfg["hostname"], site_path)
            drive = client.get_drive_by_name(site_id, drive_name)
            drive_id = drive["id"]
            folder_id = (
                client.resolve_folder_id_by_path(drive_id, folder_path)
                if folder_path
                else "root"
            )
            return [
                SourceFile(
                    name=item.get("name", "document"),
                    uri=item.get("webUrl", ""),
                    extra={"drive_id": drive_id, "item_id": item["id"]},
                )
                for item in client.list_items_recursive(drive_id, folder_id)
                if item.get("file") is not None
            ]
        except ValidationError:
            raise
        except Exception as exc:
            raise ExternalServiceError(
                "SharePoint listing failed — check microsoft.sharepoint settings "
                "and the site/drive/folder.",
                code="sharepoint_listing_failed",
                cause=exc,
            ) from exc

    def _downloader(self):
        client = self._client()

        async def download(source_file: SourceFile) -> bytes:
            return await asyncio.to_thread(
                client.download_file_content,
                source_file.extra["drive_id"],
                source_file.extra["item_id"],
            )

        return download


_service: SharePointSourceService | None = None


def get_sharepoint_source_service() -> SharePointSourceService:
    global _service
    if _service is None:
        _service = SharePointSourceService()
    return _service
