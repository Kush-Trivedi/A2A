"""The context envelope — how identity and conversation context travel
between aubrey and agents (and later agent → agent).

The bearer token on a call says WHICH service is calling; this envelope in
message.metadata says ON WHOSE BEHALF and WITH WHAT CONTEXT: tenant, user,
roles (re-enforced by Casbin wherever they land), the chat session id (the
same value used as the A2A contextId), the token-budgeted conversation
window, and the delegation hop chain (bounded + cycle-checked)."""

from dataclasses import dataclass, field
from typing import Any

ENVELOPE_NAMESPACE = "aubrey.context/v1"


@dataclass(frozen=True)
class ContextEnvelope:
    tenant_id: str
    user_id: str
    actor_id: str
    roles: tuple[str, ...] = ()
    session_id: str = ""
    correlation_id: str = ""
    purpose: str = "chat"
    delegated_from: tuple[str, ...] = ()  # hop chain, oldest first
    # recent conversation as [{"role": "user"|"assistant", "content": ...}]
    window: tuple[dict[str, str], ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            ENVELOPE_NAMESPACE: {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "actor_id": self.actor_id,
                "roles": list(self.roles),
                "session_id": self.session_id,
                "correlation_id": self.correlation_id,
                "purpose": self.purpose,
                "delegated_from": list(self.delegated_from),
                "window": [dict(w) for w in self.window],
            }
        }

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "ContextEnvelope | None":
        payload = metadata.get(ENVELOPE_NAMESPACE)
        if not isinstance(payload, dict):
            return None
        return cls(
            tenant_id=str(payload.get("tenant_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            actor_id=str(payload.get("actor_id") or ""),
            roles=tuple(str(r) for r in payload.get("roles") or ()),
            session_id=str(payload.get("session_id") or ""),
            correlation_id=str(payload.get("correlation_id") or ""),
            purpose=str(payload.get("purpose") or "chat"),
            delegated_from=tuple(str(a) for a in payload.get("delegated_from") or ()),
            window=tuple(
                {"role": str(w.get("role", "")), "content": str(w.get("content", ""))}
                for w in payload.get("window") or ()
                if isinstance(w, dict)
            ),
        )
