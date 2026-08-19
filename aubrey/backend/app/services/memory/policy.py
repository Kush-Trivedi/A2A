"""External-subject rules as a policy OBJECT (NEW_PLAN §8.3) — campaign
recipients (user_id prefix sms:/voice:) are consent-bound outsiders, not
org users, so their memory follows stricter, config-owned rules. The same
MemoryLayer code serves both subject types; ONLY this policy differs, and
the orchestrator consults it on BOTH assemble and commit — no scattered
channel branches, ever.

Defaults are deliberately minimal for external subjects: layers_enabled
[working] means recall beyond the live session is off and every long-term
commit is a structural no-op — the mechanism exists, and a campaign opts
in via yaml (agents.memory.external), e.g.:

    agents:
      memory:
        external:
          layers_enabled: [working, summary, prospective]
          facts_allowlist: ["(?i)preferred language", "(?i)callback"]
          retention_days: 30

`summary` is a pseudo-layer name gating the rolling session summary (a
derived store, not a MemoryLayer). facts_allowlist implements minimal
collection: an external fact is stored ONLY if it matches an allowlist
regex — an empty list stores none. retention_days is the external
countdown the retention sweep applies to facts/episodes (§8.3
consent-bound lifecycle); internal subjects keep the global half-lives."""

import re
from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context
from ...utils.common.logger import Logger
from .scope import MemoryScope

logger = Logger(__name__).get_logger()

_SUMMARY_PSEUDO_LAYER = "summary"


@dataclass(frozen=True)
class MemoryPolicy:
    subject_type: str  # internal | external
    # None = unrestricted (internal). A tuple restricts to those layer
    # names; "summary" gates the rolling-summary store.
    layers_enabled: tuple[str, ...] | None
    # External minimal collection: a fact must match one regex to be
    # stored; empty = store nothing. Ignored (all pass) when unrestricted.
    facts_allowlist: tuple[str, ...] = ()
    # External retention countdown in days (None = no override).
    retention_days: float | None = None

    def allows_layer(self, name: str) -> bool:
        return self.layers_enabled is None or name in self.layers_enabled

    def allows_summary(self) -> bool:
        return self.layers_enabled is None or _SUMMARY_PSEUDO_LAYER in self.layers_enabled

    def filter_facts(self, facts: list[str]) -> list[str]:
        """The facts this policy permits storing. Unrestricted scopes pass
        everything; restricted scopes keep only allowlist matches."""
        if self.layers_enabled is None:
            return list(facts)
        rules: list[re.Pattern] = []
        for raw in self.facts_allowlist:
            try:
                rules.append(re.compile(raw))
            except re.error:
                logger.error(
                    "Invalid facts_allowlist pattern skipped",
                    extra={"error_code": "memory_allowlist_pattern_invalid"},
                )
        allowed = [
            fact for fact in facts if any(rule.search(fact) for rule in rules)
        ]
        if len(allowed) < len(facts):
            # Audit trail (§8.4): counts only, never content.
            logger.info(
                "External facts filtered by allowlist",
                extra={"kept": len(allowed), "dropped": len(facts) - len(allowed)},
            )
        return allowed

    @classmethod
    def for_scope(cls, scope: MemoryScope) -> "MemoryPolicy":
        if scope.subject_type == "external":
            return _external_policy()
        return _INTERNAL_POLICY


_INTERNAL_POLICY = MemoryPolicy(subject_type="internal", layers_enabled=None)


@lru_cache(maxsize=1)
def _external_policy() -> MemoryPolicy:
    cfg = (
        (get_application_context().agents.get("memory") or {}).get("external") or {}
    )
    layers = cfg.get("layers_enabled")
    retention = cfg.get("retention_days")
    return MemoryPolicy(
        subject_type="external",
        layers_enabled=tuple(str(name) for name in layers)
        if layers is not None
        else ("working",),
        facts_allowlist=tuple(str(p) for p in (cfg.get("facts_allowlist") or ())),
        retention_days=float(retention) if retention is not None else None,
    )


def get_external_memory_policy() -> MemoryPolicy:
    """The yaml-configured external policy (used by the retention sweep for
    the §8.3 countdown without constructing a scope)."""
    return _external_policy()
