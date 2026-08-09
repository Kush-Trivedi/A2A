from pathlib import Path

from ..utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_TEMPLATE_SUFFIXES = (".md", ".txt", ".prompt")


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptRepository:
    def __init__(self) -> None:
        self._paths: list[Path] = []
        default_dir = Path(__file__).parent / "templates"
        if default_dir.is_dir():
            self.add_path(default_dir)

    def add_path(self, path: str | Path) -> None:
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            logger.warning(
                "Prompt path not found; skipping", extra={"path": str(resolved)}
            )
            return
        if resolved not in self._paths:
            self._paths.append(resolved)

    def _resolve_file(self, name: str) -> Path | None:
        candidates = (name, name.replace(".", "/"))
        for root in self._paths:
            for candidate in candidates:
                for suffix in _TEMPLATE_SUFFIXES:
                    file = root / f"{candidate}{suffix}"
                    if file.is_file():
                        return file
        return None

    def get(self, name: str, **variables: str) -> str | None:
        file = self._resolve_file(name)
        if file is None:
            return None
        try:
            template = file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.error(
                "Failed to read prompt template",
                extra={"prompt": name, "path": str(file), "error": str(exc)},
            )
            return None
        if not variables:
            return template
        return template.format_map(_SafeDict(variables))


_repository: PromptRepository | None = None


def get_prompt_repository() -> PromptRepository:
    global _repository
    if _repository is None:
        _repository = PromptRepository()
    return _repository


__all__ = ["PromptRepository", "get_prompt_repository"]
