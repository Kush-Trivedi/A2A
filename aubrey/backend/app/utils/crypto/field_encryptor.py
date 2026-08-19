"""Field-level encryption at rest (NEW_PLAN §8.2) — AES-256-GCM over
individual column values, app-side, so memory content is unreadable to
anyone holding only the database.

The key is yaml-owned (security.field_encryption.key, base64; cloud envs
point it at Key Vault via lookup:). PlaceholderPolicy decides the mode:
an unfilled key means PLAINTEXT PASSTHROUGH — local dev keeps working with
a startup warning instead of a crash, and the "enc:v1:" prefix lets
decrypt tell encrypted rows from plaintext ones, so turning encryption on
later never breaks reads of old rows. Decrypting an encrypted value
WITHOUT a key is the one hard failure: that data is unreachable until the
key is restored, and pretending otherwise would return ciphertext as text."""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...utils.common.logger import Logger
from ...utils.errors import ValidationError

logger = Logger(__name__).get_logger()

_PREFIX = "enc:v1:"
_NONCE_BYTES = 12  # 96-bit nonce — the AES-GCM standard size
_KEY_SIZES = (16, 24, 32)


class FieldEncryptor:
    """Encrypts/decrypts single string fields. Construct directly with key
    bytes (tests), or use get_field_encryptor() for the yaml-configured
    singleton."""

    def __init__(self, key: bytes | None) -> None:
        if key is not None and len(key) not in _KEY_SIZES:
            raise ValidationError(
                "security.field_encryption.key must decode to 16, 24, or 32 "
                f"bytes (got {len(key)}). Generate one with: python -c "
                "\"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
            )
        self._aesgcm = AESGCM(key) if key is not None else None

    @property
    def configured(self) -> bool:
        return self._aesgcm is not None

    def encrypt(self, value: str) -> str:
        if self._aesgcm is None:
            return value
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        return _PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value.startswith(_PREFIX):
            # Plaintext row (written in passthrough mode) — returned as-is.
            return value
        if self._aesgcm is None:
            raise ValidationError(
                "Encrypted value found but security.field_encryption.key is "
                "not configured — set the key that wrote this data."
            )
        try:
            raw = base64.b64decode(value[len(_PREFIX):], validate=True)
            nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
            return self._aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        except InvalidTag as exc:
            raise ValidationError(
                "Field decryption failed — the value was encrypted with a "
                "different security.field_encryption.key."
            ) from exc
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("Field decryption failed — malformed ciphertext.") from exc


def _key_from_config() -> bytes | None:
    raw = str(
        (get_application_context().security.get("field_encryption") or {}).get("key") or ""
    )
    if not PlaceholderPolicy.is_configured(raw):
        logger.warning(
            "security.field_encryption.key is not set — field encryption is "
            "in PLAINTEXT PASSTHROUGH mode (acceptable for local dev only)."
        )
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValidationError(
            "security.field_encryption.key is not valid base64."
        ) from exc


def decrypt_or_keep(encryptor: FieldEncryptor, value: str) -> str:
    """Best-effort read for listings: encrypted rows decrypt, legacy
    plaintext rows pass through (the enc:v1: prefix discriminates), and a
    row that CANNOT decrypt (key mismatch) is returned as stored with a
    warning — one bad row never sinks a whole listing. Use the strict
    decrypt() where an unreadable value must be an error."""
    try:
        return encryptor.decrypt(value)
    except Exception:  # noqa: BLE001 — read path stays non-fatal by design
        logger.warning("Field failed to decrypt — returning stored value unchanged")
        return value


_encryptor: FieldEncryptor | None = None


def get_field_encryptor() -> FieldEncryptor:
    global _encryptor
    if _encryptor is None:
        _encryptor = FieldEncryptor(_key_from_config())
    return _encryptor
