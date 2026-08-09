import asyncio
import io
from markitdown import MarkItDown
from ..common.logger import Logger
from ..errors import DocumentProcessingError

logger = Logger(__name__).get_logger()

_PLAINTEXT_EXTENSIONS = frozenset({".txt", ".csv", ".json", ".log", ".md"})


class MarkItDownClient:
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
                "The uploaded document is empty.",
                details={"filename": filename},
            )

        extension = self._extension(filename)

        if extension in _PLAINTEXT_EXTENSIONS or extension == "":
            decoded = self._sanitize(raw_bytes.decode("utf-8", errors="ignore"))
            if decoded:
                return decoded

        try:
            result = self._converter.convert_stream(
                io.BytesIO(raw_bytes), file_extension=extension or None
            )
            converted = self._sanitize(getattr(result, "text_content", ""))
            if converted:
                return converted
        except Exception as exc:
            logger.warning(
                "MarkItDown conversion failed; falling back to text decode.",
                extra={"source_filename": filename, "extension": extension, "error": str(exc)},
            )

        fallback = self._sanitize(raw_bytes.decode("utf-8", errors="ignore"))
        if fallback:
            return fallback

        raise DocumentProcessingError(
            "The document could not be converted to text.",
            details={"filename": filename, "extension": extension},
        )

    async def aconvert_bytes(self, raw_bytes: bytes, filename: str) -> str:
        return await asyncio.to_thread(self.convert_bytes, raw_bytes, filename)


_client: MarkItDownClient | None = None

def get_markitdown_client() -> MarkItDownClient:
    global _client
    if _client is None:
        _client = MarkItDownClient()
    return _client
