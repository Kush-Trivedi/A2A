"""M5 delegation — authenticated agent-to-agent consultation.

An agent never talks to a peer it merely knows the URL of. It asks the
platform first (POST /capability/agents/resolve): aubrey checks that the
peer is a registered ACTIVE agent the END USER's roles permit, that the
caller has declared the peer (agent manifests declare `peers`; since the
platform holds no manifests, the env yaml MIRRORS those declarations in
agents.a2a.delegation.allowed — caller agent_key -> [peer agent_keys]),
enforces the depth cap, rejects cycles, and audits the hop.

The hop chain (`delegated_from`) is signature-covered, so agents cannot
extend it themselves: resolve extends the chain SERVER-SIDE with the
caller's key and returns a fresh platform-signed envelope the caller
forwards to the peer verbatim."""

from dataclasses import dataclass
from functools import lru_cache

from sqlmodel import select

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...dto.capability import ContextEnvelopeModel
from ...entity.agents import AgentStatus, RegisteredAgentEntity
from ...utils.common.logger import Logger
from ...utils.errors import ForbiddenError, NotFoundError, ValidationError
from .envelope_signer import get_envelope_signer

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class DelegationSettings:
    max_depth: int
    # caller agent_key -> peer agent_keys the caller may consult (the yaml
    # mirror of manifest-declared peers)
    allowed: dict[str, tuple[str, ...]]


@lru_cache(maxsize=1)
def get_delegation_settings() -> DelegationSettings:
    a2a = get_application_context().agents.get("a2a") or {}
    delegation = a2a.get("delegation") or {}
    allowed = {
        str(caller).strip().lower(): tuple(
            str(peer).strip().lower() for peer in peers or ()
        )
        for caller, peers in (delegation.get("allowed") or {}).items()
    }
    return DelegationSettings(
        max_depth=int(delegation.get("max_depth") or 2),
        allowed=allowed,
    )


def extend_chain(chain: tuple[str, ...], caller_key: str) -> tuple[str, ...]:
    """The chain the PEER will see: prior hops + the calling agent."""
    return (*chain, caller_key.strip().lower())


def validate_hop(
    *,
    caller_key: str,
    peer_key: str,
    chain: tuple[str, ...],
    settings: DelegationSettings,
) -> None:
    """Pure guard logic — depth cap, cycle rejection, allowlist. Raises
    ValidationError (malformed/cyclic/deep) or ForbiddenError (undeclared
    peer); returns None when the hop is permitted."""
    caller = caller_key.strip().lower()
    peer = peer_key.strip().lower()
    normalized_chain = tuple(a.strip().lower() for a in chain)
    if not peer:
        raise ValidationError("peer_key is required for delegation.")
    if peer == caller or peer in normalized_chain:
        raise ValidationError(
            f"Delegation cycle rejected: '{peer}' already participates in "
            "this consultation chain.",
            details={"caller": caller, "peer": peer, "chain": list(normalized_chain)},
        )
    if len(normalized_chain) >= settings.max_depth:
        raise ValidationError(
            f"Delegation depth cap reached ({settings.max_depth} hops) — "
            "the consultation chain cannot be extended further.",
            details={"chain": list(normalized_chain), "max_depth": settings.max_depth},
        )
    if peer not in settings.allowed.get(caller, ()):
        raise ForbiddenError(
            f"Agent '{caller}' has not declared '{peer}' as a peer. Declare "
            "it in the agent manifest and mirror it in the platform yaml "
            "(agents.a2a.delegation.allowed).",
            details={"caller": caller, "peer": peer},
        )


class DelegationService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def resolve_peer(
        self,
        *,
        tenant_id: str,
        caller_key: str,
        peer_key: str,
        chain: tuple[str, ...],
    ) -> RegisteredAgentEntity:
        """Guards the hop, then returns the peer's registry row. The
        end-user Casbin check against the peer's permission stays with the
        route (enforce_agent_access), like every other capability."""
        validate_hop(
            caller_key=caller_key,
            peer_key=peer_key,
            chain=chain,
            settings=get_delegation_settings(),
        )
        normalized = peer_key.strip().lower()
        async with self._db.session() as session:
            peer = (
                await session.exec(
                    select(RegisteredAgentEntity).where(
                        RegisteredAgentEntity.tenant_id == tenant_id,
                        RegisteredAgentEntity.agent_key == normalized,
                    )
                )
            ).first()
        if peer is None:
            raise NotFoundError(
                f"Peer agent '{normalized}' is not registered.",
                details={"peer_key": normalized},
            )
        if peer.status != AgentStatus.ACTIVE:
            raise ForbiddenError(
                f"Peer agent '{normalized}' is not active — an admin must "
                "activate it first."
            )
        if not (peer.card_url or "").strip():
            raise ValidationError(
                f"Peer agent '{normalized}' has no card_url registered — it "
                "cannot be reached over A2A."
            )
        return peer

    def signed_envelope(
        self,
        *,
        envelope: ContextEnvelopeModel,
        tenant_id: str,
        caller_key: str,
    ) -> dict:
        """The fresh platform-signed envelope the caller forwards to the
        peer: same identity, chain extended server-side with the caller's
        key, re-signed. Shaped like the metadata block under
        aubrey.context/v1 (window/memory are NOT signature-covered — the
        caller's kit adds its local conversation context before sending)."""
        chain = extend_chain(tuple(envelope.delegated_from), caller_key)
        payload = {
            "tenant_id": tenant_id,
            "user_id": envelope.user_id,
            "actor_id": envelope.actor_id,
            "roles": list(envelope.roles),
            "session_id": envelope.session_id or "",
            "correlation_id": envelope.correlation_id or "",
            "purpose": envelope.purpose,
            "delegated_from": list(chain),
        }
        return get_envelope_signer().sign(payload)


_service: DelegationService | None = None


def get_delegation_service() -> DelegationService:
    global _service
    if _service is None:
        _service = DelegationService()
    return _service
