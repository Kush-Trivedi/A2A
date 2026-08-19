"""Query contextualization (NEW_PLAN M10a) — rewrite follow-ups into
standalone questions BEFORE routing and dispatch, using the conversation
window. "what about for children?" becomes "what is the physical therapy
copay for children?", so the router scores a real question and the
receiving agent retrieves against grounded text.

Config-owned (agents.router.contextualizer): enabled flag, prompt
template ({window}/{question}), max tokens. Fail-open: any error returns
the original question — contextualization is an optimization, never a
gate. Single-turn sessions skip the LLM call entirely."""

from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context
from ...llm.azure_foundry import get_ace_azure_foundry
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_DEFAULT_PROMPT = (
    "Rewrite the user's last message as ONE standalone question that "
    "preserves its meaning using the conversation. Keep the user's "
    "language. If it is already standalone, return it unchanged. Return "
    "ONLY the question, no explanation.\n\nConversation:\n{window}\n\n"
    "Last message: {question}"
)


@dataclass(frozen=True)
class ContextualizerSettings:
    enabled: bool
    prompt_template: str
    max_output_tokens: int


@lru_cache(maxsize=1)
def get_contextualizer_settings() -> ContextualizerSettings:
    cfg = (get_application_context().agents.get("router") or {}).get(
        "contextualizer"
    ) or {}
    return ContextualizerSettings(
        enabled=bool(cfg.get("enabled", True)),
        prompt_template=str(cfg.get("prompt_template") or _DEFAULT_PROMPT),
        max_output_tokens=int(cfg.get("max_output_tokens") or 200),
    )


class QueryContextualizer:
    def __init__(self) -> None:
        self._settings = get_contextualizer_settings()

    async def rewrite(self, *, question: str, window: list) -> str:
        """window = WindowMessage-likes with .role/.content (or dicts)."""
        if not self._settings.enabled or not window:
            return question
        lines = []
        for w in window[-8:]:
            role = getattr(w, "role", None) or (w.get("role") if isinstance(w, dict) else "")
            content = getattr(w, "content", None) or (w.get("content") if isinstance(w, dict) else "")
            if role and content:
                lines.append(f"{role}: {content}")
        if not lines:
            return question
        prompt = self._settings.prompt_template.replace(
            "{window}", "\n".join(lines)
        ).replace("{question}", question)
        try:
            rewritten = await get_ace_azure_foundry().acomplete_chat(
                messages=[{"role": "system", "content": prompt}],
                max_output_tokens=self._settings.max_output_tokens,
            )
            cleaned = str(rewritten or "").strip().strip('"')
            if cleaned and len(cleaned) < max(400, 4 * len(question)):
                return cleaned
        except Exception:  # noqa: BLE001 — optimization, never a gate
            logger.warning("Query contextualization failed — using original", exc_info=True)
        return question


_service: QueryContextualizer | None = None


def get_query_contextualizer() -> QueryContextualizer:
    global _service
    if _service is None:
        _service = QueryContextualizer()
    return _service
