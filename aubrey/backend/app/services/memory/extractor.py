"""Turn mining — one completion per committed turn that asks the model for
durable memory: stable facts AND prospects (future commitments — "remind",
"follow up", scheduled dates) as separate keys of one JSON object. Prompts
are yaml-owned (agents.memory.turn_extraction_prompt for the combined
object form; the legacy agents.memory.extraction_prompt still drives the
facts-only extract()).

Parsing is defensive by design: models wrap JSON in fences, return prose,
return bare arrays (the legacy facts-only shape — accepted as facts), or
mistype fields — anything malformed degrades to fewer (or zero) items,
never an exception. Redaction and encryption are NOT this class's job; the
layers apply both on record()."""

import json
from dataclasses import dataclass

from ...llm.azure_foundry import get_ace_azure_foundry
from ...utils.common.logger import Logger
from .settings import MemorySettings, get_memory_settings

logger = Logger(__name__).get_logger()

_MAX_FACT_CHARS = 300


@dataclass(frozen=True)
class TurnExtraction:
    facts: tuple[str, ...] = ()
    # Each: {"content": str, "due_at": str} — due_at "" when unstated.
    prospects: tuple[dict, ...] = ()


class MemoryExtractor:
    def __init__(self, settings: MemorySettings | None = None) -> None:
        self._settings = settings or get_memory_settings()

    async def extract(self, *, question: str, answer: str) -> list[str]:
        """Legacy facts-only path (agents.memory.extraction_prompt)."""
        raw = await self._complete(
            self._settings.extraction_prompt, question=question, answer=answer
        )
        if raw is None:
            return []
        return self._parse_fact_list(raw)[: self._settings.extraction_max_facts]

    async def extract_turn(self, *, question: str, answer: str) -> TurnExtraction:
        """Facts + prospects from one turn (M10c). One completion; both
        halves defensively parsed and independently capped."""
        raw = await self._complete(
            self._settings.turn_extraction_prompt, question=question, answer=answer
        )
        if raw is None:
            return TurnExtraction()
        cap = self._settings.extraction_max_facts
        facts, prospects = self._parse_turn(raw)
        return TurnExtraction(facts=tuple(facts[:cap]), prospects=tuple(prospects[:cap]))

    async def _complete(self, prompt_template: str, *, question: str, answer: str) -> str | None:
        prompt = prompt_template.replace("{question}", question).replace("{answer}", answer)
        try:
            raw = await get_ace_azure_foundry().acomplete_chat(
                messages=[{"role": "system", "content": prompt}],
            )
        except Exception:  # noqa: BLE001 — background path, never raises upward
            logger.warning("Memory extraction call failed", exc_info=True)
            return None
        return str(raw or "")

    # -- parsing ----------------------------------------------------------- #

    @staticmethod
    def _strip_fences(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        return text

    @classmethod
    def _parse_fact_list(cls, raw: str) -> list[str]:
        text = cls._strip_fences(raw)
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            parsed = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        return cls._string_items(parsed)

    @classmethod
    def _parse_turn(cls, raw: str) -> tuple[list[str], list[dict]]:
        text = cls._strip_fences(raw)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            # A bare array is the legacy facts-only shape — accept it.
            return cls._parse_fact_list(raw), []
        try:
            parsed = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return [], []
        if not isinstance(parsed, dict):
            return [], []
        raw_facts = parsed.get("facts")
        facts = cls._string_items(raw_facts) if isinstance(raw_facts, list) else []
        raw_prospects = parsed.get("prospects")
        prospects = (
            cls._prospect_items(raw_prospects) if isinstance(raw_prospects, list) else []
        )
        return facts, prospects

    @staticmethod
    def _string_items(parsed: list) -> list[str]:
        items: list[str] = []
        for item in parsed:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                # Tolerate {"fact": "..."} style objects.
                value = str(item.get("fact") or item.get("content") or "").strip()
            else:
                continue
            if value:
                items.append(value[:_MAX_FACT_CHARS])
        return items

    @staticmethod
    def _prospect_items(parsed: list) -> list[dict]:
        items: list[dict] = []
        for item in parsed:
            if isinstance(item, str):
                content, due_at = item.strip(), ""
            elif isinstance(item, dict):
                content = str(item.get("content") or item.get("prospect") or "").strip()
                raw_due = item.get("due_at")
                due_at = str(raw_due).strip() if isinstance(raw_due, str) else ""
                if due_at.lower() in ("null", "none", "unknown"):
                    due_at = ""
            else:
                continue
            if content:
                items.append({"content": content[:_MAX_FACT_CHARS], "due_at": due_at})
        return items


_extractor: MemoryExtractor | None = None


def get_memory_extractor() -> MemoryExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor()
    return _extractor
