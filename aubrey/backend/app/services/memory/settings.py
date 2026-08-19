"""Every memory knob, read once from yaml agents.memory — budgets, top-ks,
timeouts, half-lives, layer toggles, prompts. Code defaults exist only so
a yaml missing a NEW key degrades sanely (the sms_settings pattern); the
env yamls carry the real values. A layer disabled here simply never
constructs — per environment, no code change."""

from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping

from ...config.application_context import get_application_context

_DEFAULT_SUMMARY_PROMPT = (
    "You maintain a rolling summary of a conversation. Fold the new turns "
    "into the current summary: keep goals, decisions, and open questions; "
    "drop pleasantries; never invent details. Return ONLY the updated "
    "summary as plain text.\n\nCurrent summary:\n{summary}\n\nNew turns:\n{turns}"
)

_DEFAULT_EXTRACTION_PROMPT = (
    "Extract stable, reusable facts about the user or their domain from "
    "this exchange (preferences, identifiers already redacted, ongoing "
    "matters). Ignore one-off phrasing and anything speculative. Return "
    "ONLY a JSON array of short fact strings; return [] when nothing "
    "qualifies.\n\nUser: {question}\n\nAssistant: {answer}"
)

_DEFAULT_TURN_EXTRACTION_PROMPT = (
    "Mine this exchange for durable memory. Return ONLY a JSON object with "
    'two keys. "facts": stable, reusable facts about the user or their '
    "domain (preferences, ongoing matters; ignore one-off phrasing and "
    'anything speculative), as short strings. "prospects": future '
    "commitments made by either side (\"remind me\", \"follow up\", "
    "scheduled dates), each as {\"content\": short description, "
    "\"due_at\": ISO-8601 date/time or null when unstated}. Use empty "
    "lists when nothing qualifies.\n\nUser: {question}\n\nAssistant: {answer}"
)


@dataclass(frozen=True)
class MemorySettings:
    window_tokens: int          # verbatim conversation window budget (exists pre-M10b)
    summary_tokens: int         # rolling summary output cap
    facts_top_k: int            # semantic recall size
    episodes_top_k: int         # episodic recall size
    layer_timeouts_ms: Mapping[str, int]  # per-layer deadline; "default" fallback
    layers_enabled: tuple[str, ...]
    half_life_days: Mapping[str, float]   # decay half-life per store
    decay_interval_seconds: int           # background decay/purge cadence
    decay_floor: float                    # prune records below this weight
    window_recent_turns: int    # working memory: always keep the last N turns
    window_semantic_top_k: int  # working memory: older turns recalled by cosine
    extraction_max_facts: int   # cap on facts mined from one turn
    summary_prompt: str         # {summary}/{turns}
    extraction_prompt: str      # {question}/{answer} (legacy facts-only)
    turn_extraction_prompt: str  # {question}/{answer} -> {facts, prospects} JSON object
    prospects_top_k: int        # prospective recall size
    prospect_horizon_days: float  # recall prospects due within this window (or past-due)
    prospect_stale_days: float  # decay cancels open prospects this far past due
    episodic_min_turns: int     # first episode once the session reaches N messages
    episodic_every_turns: int   # then one episode every N further messages

    def timeout_seconds(self, layer_name: str) -> float:
        ms = self.layer_timeouts_ms.get(
            layer_name, self.layer_timeouts_ms.get("default", 250)
        )
        return max(int(ms), 1) / 1000.0


def _timeouts(raw) -> Mapping[str, int]:
    if isinstance(raw, dict):
        return MappingProxyType({str(k): int(v) for k, v in raw.items()})
    return MappingProxyType({"default": int(raw or 250)})


@lru_cache(maxsize=1)
def get_memory_settings() -> MemorySettings:
    cfg = get_application_context().agents.get("memory") or {}
    half_life = cfg.get("half_life_days") or {}
    return MemorySettings(
        window_tokens=int(cfg.get("window_tokens") or 2000),
        summary_tokens=int(cfg.get("summary_tokens") or 300),
        facts_top_k=int(cfg.get("facts_top_k") or 5),
        episodes_top_k=int(cfg.get("episodes_top_k") or 3),
        layer_timeouts_ms=_timeouts(cfg.get("layer_timeouts_ms") or 250),
        layers_enabled=tuple(
            str(name)
            for name in (
                cfg.get("layers_enabled")
                or ("working", "semantic", "episodic", "prospective")
            )
        ),
        half_life_days=MappingProxyType(
            {str(k): float(v) for k, v in ({"facts": 90, "episodes": 60} | dict(half_life)).items()}
        ),
        decay_interval_seconds=int(cfg.get("decay_interval_seconds") or 21600),
        decay_floor=float(cfg.get("decay_floor") or 0.05),
        window_recent_turns=int(cfg.get("window_recent_turns") or 6),
        window_semantic_top_k=int(cfg.get("window_semantic_top_k") or 4),
        extraction_max_facts=int(cfg.get("extraction_max_facts") or 5),
        summary_prompt=str(cfg.get("summary_prompt") or _DEFAULT_SUMMARY_PROMPT),
        extraction_prompt=str(cfg.get("extraction_prompt") or _DEFAULT_EXTRACTION_PROMPT),
        turn_extraction_prompt=str(
            cfg.get("turn_extraction_prompt") or _DEFAULT_TURN_EXTRACTION_PROMPT
        ),
        prospects_top_k=int(cfg.get("prospects_top_k") or 3),
        prospect_horizon_days=float(cfg.get("prospect_horizon_days") or 7),
        prospect_stale_days=float(cfg.get("prospect_stale_days") or 30),
        episodic_min_turns=int(cfg.get("episodic_min_turns") or 4),
        episodic_every_turns=int(cfg.get("episodic_every_turns") or 8),
    )
