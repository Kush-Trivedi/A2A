"""Ad-hoc file upload: multipart in, LLM-ready text out.

Any MarkItDown-supported document — or a zip of them — is converted and
returned as prepared text per document. Pass `session_id` to attach the
upload to a chat session you own: the prepared text is persisted against
that session (and only that session) so the file agent can answer from it.
Without `session_id` nothing is persisted. The upload is read in chunks and
rejected the moment it crosses the size limit, so a huge body never lands
in memory; zip expansion has its own budget inside the service (zip-bomb
guard)."""

from fastapi import APIRouter, Depends, File, Form, UploadFile

from .....dto.base import ApiEnvelope
from .....dto.documents import FailedFileModel, FileUploadResponse, PreparedFileModel
from .....security.authorization import require_permission
from .....security.dependencies import get_current_context, require_csrf
from .....security.session import SessionContext
from .....services.documents import FileUploadService, SessionDocumentService
from .....services.documents.file_upload_service import MAX_UPLOAD_BYTES
from .....utils.errors import ValidationError
from ....dependencies import (
    provide_file_upload_service,
    provide_session_document_service,
)

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
    session_id: str | None = Form(
        None,
        description="Chat session to attach the upload to — enables the file "
        "agent to answer from it. Omit for convert-and-return only.",
    ),
    context: SessionContext = Depends(get_current_context),
    service: FileUploadService = Depends(provide_file_upload_service),
    session_documents: SessionDocumentService = Depends(
        provide_session_document_service
    ),
) -> ApiEnvelope[FileUploadResponse]:
    content = await _read_limited(file)
    preparation = await service.prepare(file_name=file.filename or "", content=content)

    attached_session = (session_id or "").strip() or None
    stored = 0
    if attached_session:
        stored = await session_documents.store(
            context=context, session_id=attached_session, preparation=preparation
        )

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
            session_id=attached_session,
            stored=stored,
        ),
        message=(
            "File prepared and attached to the session."
            if attached_session
            else "File prepared."
        ),
    )
