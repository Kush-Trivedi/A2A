from typing import Any
from pydantic import Field
from ..base import StrictBaseModel

class EmbeddingRequest(StrictBaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=256)
    dimensions: int = Field(default=None, gt=1)

class EmbeddingResponse(StrictBaseModel):
    model: str
    dimensions: int 
    embeddings: list[list[float]]

class IngestTextRequest(StrictBaseModel):
    knowledge_source: str = Field(..., min_length=1, max_length=120)
    source_name: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=1)
    source_type: str | None = Field(..., min_length=1, max_length=60)
    session_id: str | None = Field(..., min_length=1, max_length=120)
    metadata: dict[str, Any] | None = Field(default=None)
    chunking_strategy: str | None = Field(
        default=None,
        max_length=40,
        description="simple | recursive | hierarchical | semantic (default from config)",
    )

class IngestSharePointRequest(StrictBaseModel):
    source_name: str = Field(..., min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    site_path: str = Field(..., min_length=1, max_length=255)
    drive_name: str = Field(..., min_length=1, max_length=120)
    folder_path: str = Field(default="", max_length=500)
    chunking_strategy: str | None = Field(default=None, max_length=40)


class IngestSharePointResponse(StrictBaseModel):
    knowledge_source: str
    files_ingested: int
    files_skipped: int
    chunk_count: int


class IngestBlobRequest(StrictBaseModel):
    source_name: str = Field(..., min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    container: str = Field(..., min_length=1, max_length=120)
    prefix: str = Field(default="", max_length=500)
    chunking_strategy: str | None = Field(default=None, max_length=40)


class IngestBlobResponse(StrictBaseModel):
    knowledge_source: str
    files_ingested: int
    files_skipped: int
    chunk_count: int


class IngestJobAcceptedResponse(StrictBaseModel):
    job_id: str
    status: str = "running"


class IngestJobStatusResponse(StrictBaseModel):
    job_id: str
    kind: str
    source_name: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(StrictBaseModel):
    document_id: str
    source_name: str
    knowledge_source: str
    chunk_count: int
    status: str