"""Searchable encryption for phone columns (NEW_PLAN §9.3, M10-S1) — the
proven AAAS pattern: the stored phone value becomes FieldEncryptor
ciphertext (unreadable at rest) while a deterministic HMAC-SHA256 of the
E.164 string lands in a separate indexed `phone_hash` column so equality
lookups still work. HMAC (not a bare hash) because phone numbers are a
tiny keyspace — without the key an attacker cannot precompute the table.

The HMAC key is the same yaml-owned field-encryption key (one secret to
rotate). In PLAINTEXT PASSTHROUGH mode (no key configured, local dev) the
HMAC runs with an empty key — lookups stay deterministic and dev keeps
working, with the same startup warning the encryptor already emits.
Display never uses the stored value: services keep the plaintext they were
called with, and audit surfaces use last-4 only."""

import hashlib
import hmac
from functools import lru_cache

from .field_encryptor import _key_from_config


class PhoneHasher:
    """Deterministic phone -> hex digest. Construct directly with key bytes
    (tests) or use get_phone_hasher() for the yaml-configured singleton."""

    def __init__(self, key: bytes | None) -> None:
        self._key = key or b""

    def hash(self, phone: str) -> str:
        cleaned = (phone or "").strip()
        return hmac.new(self._key, cleaned.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def last4(phone: str) -> str:
        cleaned = (phone or "").strip()
        return cleaned[-4:] if cleaned else ""


@lru_cache(maxsize=1)
def get_phone_hasher() -> PhoneHasher:
    return PhoneHasher(_key_from_config())
