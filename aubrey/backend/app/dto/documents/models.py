from datetime import datetime

from ..base import StrictBaseModel


class RegisterConnectionRequest(StrictBaseModel):
    team_key: str
    connection_key: str
    source_type: str  # blob | sharepoint
    description: str = ""
    # blob: {account_url, container} — sharepoint: {site_path, drive_name}
    config: dict[str, str]


class ConnectionModel(StrictBaseModel):
    id: str
    team_key: str
    connection_key: str
    source_type: str
    description: str
    config: dict[str, str]
    created_at: datetime
    updated_at: datetime


class BlobIngestRequest(StrictBaseModel):
    team_key: str
    agent_key: str
    connection_key: str
    prefix: str = ""
    file_name: str | None = None
    blob_url: str | None = None  # single-file fetch — the Event Grid path


class SharePointIngestRequest(StrictBaseModel):
    team_key: str
    agent_key: str
    connection_key: str
    folder_path: str = ""
    file_name: str | None = None


class IngestResultModel(StrictBaseModel):
    batch_id: str
    processed: int  # new content: converted + stored
    linked: int     # content existed: granted to this agent, no re-conversion
    skipped: int    # already granted to this agent
    failed: int


class PreparedFileModel(StrictBaseModel):
    file_name: str
    sha256: str
    characters: int
    text: str  # LLM-ready markdown


class FailedFileModel(StrictBaseModel):
    file_name: str
    reason: str


class FileUploadResponse(StrictBaseModel):
    upload_name: str
    size_bytes: int
    prepared: list[PreparedFileModel]
    failed: list[FailedFileModel]
