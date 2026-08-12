import asyncio
import io

from markitdown import MarkItDown

from ..common.logger import Logger
from ..errors import DocumentProcessingError

logger = Logger(__name__).get_logger()

# Plain-text formats are decoded directly (MarkItDown adds nothing there);
# EVERYTHING else goes to MarkItDown, whatever the type — no allowlist.
_PLAINTEXT_EXTENSIONS = frozenset({".txt", ".csv", ".json", ".log", ".md"})


class MarkItDownClient:
    """Converts any MarkItDown-supported document to text.

    A file MarkItDown cannot convert FAILS with a clear error — it is never
    silently text-decoded, so binary junk can't leak into the knowledge base.
    Callers count the failure and continue the batch.
    """

    def __init__(self, converter: MarkItDown | None = None) -> None:
        self._converter = converter or MarkItDown()

    @staticmethod
    def _extension(filename: str) -> str:
        name = (filename or "").strip().lower()
        if "." not in name:
            return ""
        return f".{name.rsplit('.', 1)[-1]}"

    @staticmethod
    def _sanitize(text: str | None) -> str:
        return text.replace("\x00", "").strip() if text else ""

    def convert_bytes(self, raw_bytes: bytes, filename: str) -> str:
        if not raw_bytes:
            raise DocumentProcessingError(
                "The document is empty.", details={"filename": filename}
            )

        extension = self._extension(filename)
        if extension in _PLAINTEXT_EXTENSIONS:
            decoded = self._sanitize(raw_bytes.decode("utf-8", errors="ignore"))
            if decoded:
                return decoded
            raise DocumentProcessingError(
                "The text document decoded to nothing.",
                details={"filename": filename},
            )

        try:
            result = self._converter.convert_stream(
                io.BytesIO(raw_bytes), file_extension=extension or None
            )
        except Exception as exc:
            raise DocumentProcessingError(
                "The document type is not supported for text conversion.",
                details={"filename": filename, "extension": extension},
                cause=exc,
            ) from exc

        converted = self._sanitize(getattr(result, "text_content", ""))
        if not converted:
            raise DocumentProcessingError(
                "The document converted to empty text.",
                details={"filename": filename, "extension": extension},
            )
        return converted

    async def aconvert_bytes(self, raw_bytes: bytes, filename: str) -> str:
        return await asyncio.to_thread(self.convert_bytes, raw_bytes, filename)


_client: MarkItDownClient | None = None


def get_markitdown_client() -> MarkItDownClient:
    global _client
    if _client is None:
        _client = MarkItDownClient()
    return _client
