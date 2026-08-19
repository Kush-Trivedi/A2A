"""Rolling session summary — the compressed past that keeps long sessions
coherent after the verbatim window scrolls off. Folds the newest turns
into the previous summary with one non-streaming completion; the prompt is
yaml-owned (agents.memory.summary_prompt, {summary}/{turns}) and the
output is capped at summary_tokens. Fail-open: any LLM failure returns the
previous summary unchanged — a summary is an optimization, never a gate."""

from ...llm.azure_foundry import get_ace_azure_foundry
from ...utils.common.logger import Logger
from .settings import MemorySettings, get_memory_settings

logger = Logger(__name__).get_logger()


class SessionSummarizer:
    def __init__(self, settings: MemorySettings | None = None) -> None:
        self._settings = settings or get_memory_settings()

    async def update(self, *, previous: str, turns: list[dict[str, str]]) -> str:
        """The summary with `turns` folded in; `previous` on any failure."""
        lines = [
            f"{t.get('role', '')}: {t.get('content', '')}"
            for t in turns
            if t.get("role") and t.get("content")
        ]
        if not lines:
            return previous
        prompt = self._settings.summary_prompt.replace(
            "{summary}", previous or "(none yet)"
        ).replace("{turns}", "\n".join(lines))
        try:
            updated = await get_ace_azure_foundry().acomplete_chat(
                messages=[{"role": "system", "content": prompt}],
                max_output_tokens=self._settings.summary_tokens,
            )
            cleaned = str(updated or "").strip()
            return cleaned or previous
        except Exception:  # noqa: BLE001 — background path, never raises upward
            logger.warning("Session summary update failed — keeping previous", exc_info=True)
            return previous


_summarizer: SessionSummarizer | None = None


def get_session_summarizer() -> SessionSummarizer:
    global _summarizer
    if _summarizer is None:
        _summarizer = SessionSummarizer()
    return _summarizer
