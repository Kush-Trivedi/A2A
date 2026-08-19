"""Envelope signing (NEW_PLAN §9.4, M10-S3) — the capability-plane context
envelope stops being self-asserted.

Aubrey HMAC-signs the identity fields of every outbound envelope at
dispatch; capability endpoints verify signature + freshness before serving.
A team-token holder can no longer mint an arbitrary user_id/roles/session —
the only envelopes that pass are ones the platform actually issued (and,
for delegation, re-issued with the extended hop chain — agents cannot
extend `delegated_from` themselves because it is signature-covered).

The key is yaml-owned (security.envelope_signing.key, base64; cloud envs
point at Key Vault via lookup:). PlaceholderPolicy decides the mode: an
unfilled key means signing is DISABLED — outbound envelopes go unsigned and
verification accepts unsigned envelopes, so local dev keeps working with a
startup warning instead of a crash (same passthrough philosophy as
FieldEncryptor)."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...utils.common.logger import Logger
from ...utils.errors import ForbiddenError, ValidationError

logger = Logger(__name__).get_logger()

_DEFAULT_MAX_AGE_SECONDS = 300.0
# Tolerated clock drift between signer and verifier for "issued in the
# future" timestamps — beyond this the envelope is treated as forged.
_CLOCK_SKEW_SECONDS = 30.0
_MIN_KEY_BYTES = 16

_UNSIGNED_MESSAGE = "envelope not signed by platform"


class EnvelopeSigner:
    """HMAC-SHA256 over a canonical JSON of the envelope identity fields
    (tenant_id, user_id, actor_id, roles, session_id, purpose,
    delegated_from, issued_at). Construct directly with key bytes (tests),
    or use get_envelope_signer() for the yaml-configured singleton."""

    def __init__(
        self, key: bytes | None, max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS
    ) -> None:
        if key is not None and len(key) < _MIN_KEY_BYTES:
            raise ValidationError(
                "security.envelope_signing.key must decode to at least "
                f"{_MIN_KEY_BYTES} bytes (got {len(key)}). Generate one with: "
                "python -c \"import os,base64;"
                "print(base64.b64encode(os.urandom(32)).decode())\""
            )
        self._key = key
        self._max_age = float(max_age_seconds)

    @property
    def configured(self) -> bool:
        return self._key is not None

    @staticmethod
    def _canonical(
        payload: Mapping[str, Any], issued_at: str, tenant_id: str | None = None
    ) -> bytes:
        """Canonical JSON of exactly the signature-covered fields — sorted
        keys, no whitespace, everything coerced to strings, so signer and
        verifier agree byte-for-byte."""
        material = {
            "tenant_id": str(
                tenant_id if tenant_id is not None else payload.get("tenant_id") or ""
            ),
            "user_id": str(payload.get("user_id") or ""),
            "actor_id": str(payload.get("actor_id") or ""),
            "roles": [str(r) for r in payload.get("roles") or ()],
            "session_id": str(payload.get("session_id") or ""),
            "purpose": str(payload.get("purpose") or ""),
            "delegated_from": [str(a) for a in payload.get("delegated_from") or ()],
            "issued_at": issued_at,
        }
        return json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    def sign(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Returns the envelope metadata block with {"sig", "issued_at"}
        added. In passthrough mode (no key) the payload is returned
        unchanged — unsigned, and verification accepts it."""
        data = dict(payload)
        if self._key is None:
            return data
        issued_at = datetime.now(timezone.utc).isoformat()
        digest = hmac.new(
            self._key, self._canonical(data, issued_at), hashlib.sha256
        ).digest()
        data["sig"] = base64.b64encode(digest).decode("ascii")
        data["issued_at"] = issued_at
        return data

    def verify(
        self, payload: Mapping[str, Any], *, tenant_id: str | None = None
    ) -> None:
        """Raises ForbiddenError unless the payload carries a valid, fresh
        platform signature. `tenant_id` overrides the payload's own claim —
        capability callers pass the TOKEN's tenant, which binds the
        signature to it (a replay under another tenant's token fails).
        Passthrough mode (no key) accepts everything, signed or not."""
        if self._key is None:
            return
        sig = str(payload.get("sig") or "")
        issued_at = str(payload.get("issued_at") or "")
        if not sig or not issued_at:
            raise ForbiddenError(
                _UNSIGNED_MESSAGE, details={"reason": "signature_missing"}
            )
        try:
            issued = datetime.fromisoformat(issued_at)
        except ValueError as exc:
            raise ForbiddenError(
                _UNSIGNED_MESSAGE, details={"reason": "issued_at_malformed"}
            ) from exc
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - issued).total_seconds()
        if age > self._max_age or age < -_CLOCK_SKEW_SECONDS:
            raise ForbiddenError(
                _UNSIGNED_MESSAGE,
                details={"reason": "signature_stale", "age_seconds": int(age)},
            )
        try:
            provided = base64.b64decode(sig, validate=True)
        except Exception as exc:  # noqa: BLE001 — any bad encoding is a bad signature
            raise ForbiddenError(
                _UNSIGNED_MESSAGE, details={"reason": "signature_malformed"}
            ) from exc
        expected = hmac.new(
            self._key, self._canonical(payload, issued_at, tenant_id), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, provided):
            raise ForbiddenError(
                _UNSIGNED_MESSAGE, details={"reason": "signature_invalid"}
            )


def _signer_from_config() -> EnvelopeSigner:
    cfg = get_application_context().security.get("envelope_signing") or {}
    raw = str(cfg.get("key") or "")
    max_age = float(cfg.get("max_age_seconds") or _DEFAULT_MAX_AGE_SECONDS)
    if not PlaceholderPolicy.is_configured(raw):
        logger.warning(
            "security.envelope_signing.key is not set — envelope signing is "
            "DISABLED; capability endpoints accept unsigned envelopes "
            "(acceptable for local dev only)."
        )
        return EnvelopeSigner(None, max_age)
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValidationError(
            "security.envelope_signing.key is not valid base64."
        ) from exc
    return EnvelopeSigner(key, max_age)


_signer: EnvelopeSigner | None = None


def get_envelope_signer() -> EnvelopeSigner:
    global _signer
    if _signer is None:
        _signer = _signer_from_config()
    return _signer
