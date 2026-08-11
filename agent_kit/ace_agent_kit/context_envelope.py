from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

CONTEXT_NAMESPACE = "ace.context/v1"


@dataclass(frozen=True)
class ContextEnvelope:
    tenant_id: str
    actor_id: str
    user_id: str = ""
    roles: tuple[str, ...] = ()
    correlation_id: str = ""
    chat_session_id: str = ""
    purpose: str = "chat"
    delegated_from: str = ""
    delegation_reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "user_id": self.user_id,
            "roles": list(self.roles),
            "correlation_id": self.correlation_id,
            "chat_session_id": self.chat_session_id,
            "purpose": self.purpose,
            "delegated_from": self.delegated_from,
            "delegation_reason": self.delegation_reason,
        }

    def to_metadata(self) -> dict[str, Any]:
        return {CONTEXT_NAMESPACE: self.to_payload()}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "ContextEnvelope | None":
        if not metadata:
            return None
        payload = metadata.get(CONTEXT_NAMESPACE)
        if not isinstance(payload, Mapping):
            return None
        return cls(
            tenant_id=str(payload.get("tenant_id", "") or ""),
            actor_id=str(payload.get("actor_id", "") or ""),
            user_id=str(payload.get("user_id", "") or ""),
            roles=tuple(str(r) for r in payload.get("roles", []) or []),
            correlation_id=str(payload.get("correlation_id", "") or ""),
            chat_session_id=str(payload.get("chat_session_id", "") or ""),
            purpose=str(payload.get("purpose", "") or "chat"),
            delegated_from=str(payload.get("delegated_from", "") or ""),
            delegation_reason=str(payload.get("delegation_reason", "") or ""),
        )

    def with_delegation(self, *, delegated_from: str, reason: str) -> "ContextEnvelope":
        return replace(self, delegated_from=delegated_from, delegation_reason=reason)
