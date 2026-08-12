import io
import zipfile

from ..common.logger import Logger
from ..errors import DocumentProcessingError

logger = Logger(__name__).get_logger()

# Archive/OS bookkeeping entries — never documents.
_JUNK_PREFIXES = ("__MACOSX/",)
_JUNK_NAMES = (".DS_Store", "Thumbs.db")


class ZipExtractor:
    """Walks a zip archive (folders inside folders, zips inside zips) and
    yields every real file as (relative_name, bytes). Nested archives are
    expanded up to `max_depth` levels; each inner file becomes its own
    document named `<zip>/<inner/path>`."""

    _ZIP_MAGIC = b"PK\x03\x04"

    def __init__(self, max_depth: int = 2) -> None:
        self._max_depth = max_depth

    @classmethod
    def is_zip(cls, filename: str, raw_bytes: bytes) -> bool:
        if (filename or "").strip().lower().endswith(".zip"):
            return True
        return raw_bytes[:4] == cls._ZIP_MAGIC

    def extract(self, raw_bytes: bytes, source_name: str) -> list[tuple[str, bytes]]:
        return self._extract(raw_bytes, source_name, depth=0)

    def _extract(
        self, raw_bytes: bytes, source_name: str, depth: int
    ) -> list[tuple[str, bytes]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile as exc:
            raise DocumentProcessingError(
                "The archive is not a valid zip file.",
                details={"filename": source_name},
                cause=exc,
            ) from exc

        files: list[tuple[str, bytes]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.startswith(_JUNK_PREFIXES) or name.rsplit("/", 1)[-1] in _JUNK_NAMES:
                continue
            content = archive.read(info)
            qualified = f"{source_name}/{name}"
            if self.is_zip(name, content):
                if depth + 1 > self._max_depth:
                    logger.warning(
                        "Nested archive beyond max depth skipped",
                        extra={"filename": qualified, "max_depth": self._max_depth},
                    )
                    continue
                files.extend(self._extract(content, qualified, depth + 1))
            else:
                files.append((qualified, content))
        return files
