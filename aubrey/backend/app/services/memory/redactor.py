"""Redact-before-store (NEW_PLAN §8.1) — the gate in front of every memory
write. Facts and episodes must read "member asked about knee MRI appeal",
never carry the member's SSN; embeddings are computed AFTER this pass so
vectors never encode raw identifiers.

Rules are config-owned (agents.memory.redaction.patterns, kind -> regex)
merged OVER built-in defaults — yaml can add or override kinds, but an
absent section can never silently disable a security control. Two
behaviors by kind: drop_kinds (credentials/secrets) reject the ENTIRE
record with an alert — secrets are never stored in any form under any
condition — while every other kind is replaced in place with
[REDACTED-<kind>]. Alerts log detection counts only, never content
(§8.4)."""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ...config.application_context import get_application_context
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()

# Order matters: drop kinds short-circuit; among replacements, longer
# spans (cards) go before subsumable ones (phones).
_DEFAULT_PATTERNS: dict[str, str] = {
    "credential": (
        r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"
        r"|\b(?:api[_-]?key|apikey|client[_-]?secret|secret|passwd|password|token|credential)\b"
        r"\s*[:=]\s*\S{6,})"
    ),
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d{4}[ -]?){3}\d{3,4}\b",
    "mrn": r"(?i)\bmrn[:#\s-]*\d{5,12}\b",
    "phone": r"(?:\+?1[ .-]?)?(?:\(\d{3}\)|\b\d{3})[ .-]?\d{3}[ .-]?\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
}
_DEFAULT_DROP_KINDS = ("credential",)


@dataclass(frozen=True)
class RedactionResult:
    text: str
    dropped: bool  # a drop-kind matched — do not store this record at all
    hits: dict[str, int] = field(default_factory=dict)  # kind -> match count


class MemoryRedactor:
    def __init__(
        self,
        patterns: dict[str, str] | None = None,
        drop_kinds: tuple[str, ...] | None = None,
    ) -> None:
        merged = dict(_DEFAULT_PATTERNS)
        merged.update(patterns or {})
        self._drop_kinds = drop_kinds if drop_kinds is not None else _DEFAULT_DROP_KINDS
        self._rules: dict[str, re.Pattern] = {}
        for kind, raw in merged.items():
            try:
                self._rules[kind] = re.compile(raw)
            except re.error:
                # One bad yaml regex must not disable the rest of the set.
                logger.error(
                    "Invalid redaction pattern skipped",
                    extra={"kind": kind, "error_code": "redaction_pattern_invalid"},
                )

    def redact(self, text: str) -> RedactionResult:
        hits: dict[str, int] = {}
        for kind in self._drop_kinds:
            rule = self._rules.get(kind)
            if rule is None:
                continue
            count = len(rule.findall(text))
            if count:
                hits[kind] = count
                logger.error(
                    "Credential material detected in memory content — record dropped",
                    extra={"kind": kind, "matches": count,
                           "error_code": "memory_credential_dropped"},
                )
                return RedactionResult(text="", dropped=True, hits=hits)

        redacted = text
        for kind, rule in self._rules.items():
            if kind in self._drop_kinds:
                continue
            redacted, count = rule.subn(f"[REDACTED-{kind}]", redacted)
            if count:
                hits[kind] = count
        if hits:
            # Audit trail (§8.4): counts and kinds only, never the content.
            logger.info(
                "Memory content redacted",
                extra={"kinds": sorted(hits), "matches": sum(hits.values())},
            )
        return RedactionResult(text=redacted, dropped=False, hits=hits)


@lru_cache(maxsize=1)
def get_memory_redactor() -> MemoryRedactor:
    cfg = (get_application_context().agents.get("memory") or {}).get("redaction") or {}
    patterns = {str(k): str(v) for k, v in (cfg.get("patterns") or {}).items()}
    raw_drop = cfg.get("drop_kinds")
    drop_kinds = tuple(str(k) for k in raw_drop) if raw_drop is not None else None
    return MemoryRedactor(patterns=patterns, drop_kinds=drop_kinds)
