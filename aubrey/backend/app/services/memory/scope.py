"""Who a memory belongs to — the one identity object every layer keys on.

The session is the unit of memory, the user is the unit of recall
(NEW_PLAN §1): web, SMS, Teams, and voice all resolve to the same triple,
so channels get identical memory behavior with no channel-specific code.
`channel` is informational only and never branches logic. `subject_type`
is DERIVED, not passed — external subjects (campaign recipients outside
Entra) are recognized by their user_id prefix, so no caller can
misclassify a subject and stricter external rules (§8.3, M10c) attach
automatically."""

from dataclasses import dataclass

_EXTERNAL_PREFIXES = ("sms:", "voice:")


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: str
    user_id: str  # "sms:+1..." and Entra object ids identical here
    session_id: str
    channel: str = "web"  # informational only — never branches logic

    @property
    def subject_type(self) -> str:
        """internal (org user) | external (consent-bound campaign subject)."""
        if self.user_id.startswith(_EXTERNAL_PREFIXES):
            return "external"
        return "internal"
