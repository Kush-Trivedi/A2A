import time
from typing import Any
import httpx
from ..utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_CACHE_TTL_SECONDS = 43_200

class JWKSCache:
    def __init__(self, jwks_uri: str) -> None:
        self._jwks_uri = jwks_uri
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0

    async def get_jwk(self, kid: str) -> dict[str, Any]:
        if self._is_stale():
            await self._refresh()

        if kid not in self._keys:
            await self._refresh()

        if kid not in self._keys:
            raise ValueError(
                f"JWKS kid {kid!r} not found at {self._jwks_uri!r}. "
                "The signing key may have been rotated or the kid is forged."
            )

        return self._keys[kid]

    def _is_stale(self) -> bool:
        return time.monotonic() - self._fetched_at > _CACHE_TTL_SECONDS

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self._jwks_uri)
            response.raise_for_status()
            data = response.json()

        self._keys = {
            entry["kid"]: entry
            for entry in data.get("keys", [])
            if entry.get("use") in {"sig", None}
        }
        self._fetched_at = time.monotonic()
        logger.info(
            "[blue]JWKS cache refreshed",
            extra={"signing_keys": len(self._keys)},
        )
