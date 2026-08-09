import base64
import json
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config.application_context import get_application_context
from ..config.settings_validator import PlaceholderPolicy
from ..utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_PREFIX = "enc::"


class FieldEncryptor:
    """AES-256-GCM at-rest encryption for sensitive columns (PHI: SMS bodies,
    phone numbers). Key comes from yaml `security.field_encryption_key`
    (Key Vault `lookup:` ready — never an env var, never in code).

    When no key is configured values pass through as plaintext and a warning
    is logged once — the startup validator also flags the missing key, so it
    is loud, not silent (improvement over the reference implementation).
    """

    def __init__(self, key_material: str | None = None) -> None:
        raw = key_material
        if raw is None:
            raw = str(
                get_application_context().security.get("field_encryption_key") or ""
            )
        self._key: bytes | None = None
        if PlaceholderPolicy.is_configured(raw):
            digest = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)) if len(raw) in (43, 44) else raw.encode()
            # Accept either a base64url 32-byte key or any passphrase (padded/truncated).
            self._key = digest[:32].ljust(32, b"0")
        else:
            logger.warning(
                "Field encryption key not configured — sensitive fields stored in "
                "plaintext. Set security.field_encryption_key in the env yaml."
            )

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encrypt(self, value: str | None) -> str | None:
        if value is None or not self.enabled or value.startswith(_PREFIX):
            return value
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode(), None)
        envelope = {
            "n": base64.b64encode(nonce).decode(),
            "c": base64.b64encode(ciphertext).decode(),
        }
        return _PREFIX + base64.b64encode(json.dumps(envelope).encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if value is None or not value.startswith(_PREFIX):
            return value
        if not self.enabled:
            return "[encrypted]"
        envelope = json.loads(base64.b64decode(value[len(_PREFIX):]))
        nonce = base64.b64decode(envelope["n"])
        ciphertext = base64.b64decode(envelope["c"])
        return AESGCM(self._key).decrypt(nonce, ciphertext, None).decode()


@lru_cache(maxsize=1)
def get_field_encryptor() -> FieldEncryptor:
    return FieldEncryptor()
