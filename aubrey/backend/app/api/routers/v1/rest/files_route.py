"""Ad-hoc file upload: multipart in, LLM-ready text out.

Any MarkItDown-supported document — or a zip of them — is converted and
returned as prepared text per document. Nothing is persisted; the upcoming
analysis endpoint will pass this prepared text to the model. The upload is
read in chunks and rejected the moment it crosses the size limit, so a huge
body never lands in memory; zip expansion has its own budget inside the
service (zip-bomb guard)."""

from fastapi import APIRouter, Depends, File, UploadFile

from .....dto.base import ApiEnvelope
from .....dto.documents import FailedFileModel, FileUploadResponse, PreparedFileModel
from .....security.authorization import require_permission
from .....security.dependencies import require_csrf
from .....services.documents import FileUploadService
from .....services.documents.file_upload_service import MAX_UPLOAD_BYTES
from .....utils.errors import ValidationError
from ....dependencies import provide_file_upload_service

files_router = APIRouter(prefix="/files", tags=["Files"])

_FILES_OBJ = "/api/v1/files"
_READ_CHUNK = 1024 * 1024


async def _read_limited(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_READ_CHUNK):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ValidationError(
                f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                "upload limit.",
                details={"file_name": upload.filename or ""},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@files_router.post(
    "/upload",
    response_model=ApiEnvelope[FileUploadResponse],
    dependencies=[Depends(require_csrf), Depends(require_permission(_FILES_OBJ, "POST"))],
)
async def upload_file(
    file: UploadFile = File(
        ..., description="Any MarkItDown-supported document, or a zip of them."
    ),
    service: FileUploadService = Depends(provide_file_upload_service),
) -> ApiEnvelope[FileUploadResponse]:
    content = await _read_limited(file)
    preparation = await service.prepare(file_name=file.filename or "", content=content)
    return ApiEnvelope(
        data=FileUploadResponse(
            upload_name=preparation.upload_name,
            size_bytes=preparation.size_bytes,
            prepared=[
                PreparedFileModel(
                    file_name=p.file_name,
                    sha256=p.sha256,
                    characters=p.characters,
                    text=p.text,
                )
                for p in preparation.prepared
            ],
            failed=[
                FailedFileModel(file_name=f.file_name, reason=f.reason)
                for f in preparation.failed
            ],
        ),
        message="File prepared.",
    )
