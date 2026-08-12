"""Ad-hoc upload preparation: a user uploads ONE file (any MarkItDown-
supported type, or a zip of them) and gets back LLM-ready text per document.
Nothing is persisted — this is the preprocessing step for the upcoming
analysis endpoint, which will hand the prepared text to the model.

Two size gates: the upload itself is capped, and zip extraction has its own
cumulative budget — a small archive that EXPANDS huge (zip bomb) fails loud
inside ZipExtractor instead of exhausting memory."""

import hashlib
from dataclasses import dataclass

from ...utils.common.logger import Logger
from ...utils.documents import MarkItDownClient, ZipExtractor, get_markitdown_client
from ...utils.errors import DocumentProcessingError, ValidationError

logger = Logger(__name__).get_logger()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class PreparedFile:
    file_name: str
    sha256: str
    characters: int
    text: str


@dataclass(frozen=True)
class FailedFile:
    file_name: str
    reason: str


@dataclass(frozen=True)
class UploadPreparation:
    upload_name: str
    size_bytes: int
    prepared: list[PreparedFile]
    failed: list[FailedFile]


class FileUploadService:
    def __init__(
        self,
        markitdown: MarkItDownClient | None = None,
        zips: ZipExtractor | None = None,
    ) -> None:
        self._markitdown = markitdown or get_markitdown_client()
        self._zips = zips or ZipExtractor(max_total_bytes=MAX_EXTRACTED_BYTES)

    async def prepare(self, *, file_name: str, content: bytes) -> UploadPreparation:
        name = (file_name or "").strip()
        if not name:
            raise ValidationError("The upload has no file name.")
        if not content:
            raise ValidationError(
                "The uploaded file is empty.", details={"file_name": name}
            )
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValidationError(
                f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                "upload limit.",
                details={"file_name": name, "size_bytes": len(content)},
            )

        if ZipExtractor.is_zip(name, content):
            documents = self._zips.extract(content, name)
            if not documents:
                raise ValidationError(
                    "The archive contains no documents.", details={"file_name": name}
                )
        else:
            documents = [(name, content)]

        prepared: list[PreparedFile] = []
        failed: list[FailedFile] = []
        for document_name, document_bytes in documents:
            try:
                text = await self._markitdown.aconvert_bytes(document_bytes, document_name)
            except DocumentProcessingError as exc:
                failed.append(FailedFile(file_name=document_name, reason=exc.message))
                continue
            prepared.append(
                PreparedFile(
                    file_name=document_name,
                    sha256=hashlib.sha256(document_bytes).hexdigest(),
                    characters=len(text),
                    text=text,
                )
            )

        if not prepared:
            raise DocumentProcessingError(
                "No document in the upload could be converted to text.",
                details={"failed": [f.file_name for f in failed]},
            )
        logger.info(
            "Upload prepared",
            extra={
                "upload_name": name,
                "prepared": len(prepared),
                "failed": len(failed),
            },
        )
        return UploadPreparation(
            upload_name=name,
            size_bytes=len(content),
            prepared=prepared,
            failed=failed,
        )


_service: FileUploadService | None = None


def get_file_upload_service() -> FileUploadService:
    global _service
    if _service is None:
        _service = FileUploadService()
    return _service
