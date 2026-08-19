"""Query contextualization (NEW_PLAN M10a) — rewrite follow-ups into
standalone questions BEFORE routing and dispatch, using the conversation
window. "what about for children?" becomes "what is the physical therapy
copay for children?", so the router scores a real question and the
receiving agent retrieves against grounded text.

Latency posture (the LLM rewrite is the one real cost on the routing
path): it runs only when it can actually help. Two cheap gates skip the
call entirely — no window (first turn), and self-contained questions
(long enough and free of the dangling references a follow-up carries).
When it does run, it uses a FAST model (yaml `model`, e.g. a Haiku-class
deployment) so the added latency is a few hundred ms, not seconds, and
every call logs its wall time so sluggishness is measurable, not guessed.
Fail-open throughout: any error or timeout returns the original question.

Config-owned (agents.router.contextualizer): enabled, model,
prompt_template ({window}/{question}), max_output_tokens,
min_chars_to_skip, follow_up_markers."""

import re
import time
from dataclasses import dataclass, field
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

# Cheap signal that a message DEPENDS on prior turns (a pronoun, an
# ellipsis, a comparative, a bare continuation). Config can extend these.
_DEFAULT_FOLLOW_UP_MARKERS = (
    "it", "its", "that", "this", "these", "those", "they", "them", "their",
    "he", "she", "his", "her", "one", "ones", "same", "there",
    "what about", "how about", "and ", "also", "too", "instead", "then",
    "why not", "which", "the other", "more", "less", "another",
)


@dataclass(frozen=True)
class ContextualizerSettings:
    enabled: bool
    model: str
    prompt_template: str
    max_output_tokens: int
    min_chars_to_skip: int
    follow_up_markers: tuple[str, ...] = field(default_factory=tuple)


@lru_cache(maxsize=1)
def get_contextualizer_settings() -> ContextualizerSettings:
    cfg = (get_application_context().agents.get("router") or {}).get(
        "contextualizer"
    ) or {}
    markers = cfg.get("follow_up_markers")
    return ContextualizerSettings(
        enabled=bool(cfg.get("enabled", True)),
        model=str(cfg.get("model") or ""),  # "" = platform default chat model
        prompt_template=str(cfg.get("prompt_template") or _DEFAULT_PROMPT),
        max_output_tokens=int(cfg.get("max_output_tokens") or 200),
        min_chars_to_skip=int(cfg.get("min_chars_to_skip") or 60),
        follow_up_markers=tuple(
            str(m).strip().lower() for m in (markers or _DEFAULT_FOLLOW_UP_MARKERS)
            if str(m).strip()
        ),
    )


class QueryContextualizer:
    def __init__(self) -> None:
        self._settings = get_contextualizer_settings()

    def _is_self_contained(self, question: str) -> bool:
        """A long question with no follow-up markers stands on its own —
        rewriting it costs latency for nothing, so we skip the LLM call."""
        text = (question or "").strip().lower()
        if len(text) < self._settings.min_chars_to_skip:
            return False  # short questions are the risky follow-ups
        words = set(re.findall(r"[a-z']+", text))
        for marker in self._settings.follow_up_markers:
            if " " in marker:
                if marker in text:
                    return False
            elif marker in words:
                return False
        return True

    async def rewrite(self, *, question: str, window: list) -> str:
        """window = WindowMessage-likes with .role/.content (or dicts)."""
        if not self._settings.enabled or not window:
            return question
        if self._is_self_contained(question):
            return question  # gate: no rewrite needed, no latency paid
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
        started = time.monotonic()
        try:
            rewritten = await get_ace_azure_foundry().acomplete_chat(
                messages=[{"role": "system", "content": prompt}],
                model=self._settings.model or None,
                max_output_tokens=self._settings.max_output_tokens,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            cleaned = str(rewritten or "").strip().strip('"')
            if cleaned and len(cleaned) < max(400, 4 * len(question)):
                logger.info(
                    "Query contextualized",
                    extra={"latency_ms": elapsed_ms, "rewritten": cleaned != question},
                )
                return cleaned
            logger.info("Query contextualization no-op", extra={"latency_ms": elapsed_ms})
        except Exception:  # noqa: BLE001 — optimization, never a gate
            logger.warning(
                "Query contextualization failed — using original",
                extra={"latency_ms": int((time.monotonic() - started) * 1000)},
                exc_info=True,
            )
        return question


_service: QueryContextualizer | None = None


def get_query_contextualizer() -> QueryContextualizer:
    global _service
    if _service is None:
        _service = QueryContextualizer()
    return _service
