import asyncio
import time
from dataclasses import dataclass
from functools import lru_cache

from ..config.application_context import get_application_context
from ..utils.common.logger import Logger
from ..utils.errors import TooManyRequestsError

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class RateLimitSettings:
    enabled: bool
    requests_per_minute: int
    burst: int


@lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    cfg = get_application_context().security.get("rate_limit", {}) or {}
    return RateLimitSettings(
        enabled=bool(cfg.get("enabled", False)),
        requests_per_minute=int(cfg.get("requests_per_minute", 30)),
        burst=int(cfg.get("burst", 10)),
    )


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """Per-actor token bucket for the expensive routes (chat, ingestion).

    Defense in depth behind the edge (APIM/Front Door does volume): stops a
    single runaway session from burning LLM quota. In-memory per replica —
    intentionally simple; the edge owns global limits.
    """

    def __init__(self, settings: RateLimitSettings | None = None) -> None:
        self._settings = settings or get_rate_limit_settings()
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        if not self._settings.enabled or not key:
            return
        rate_per_second = self._settings.requests_per_minute / 60.0
        capacity = float(max(1, self._settings.burst))
        now = time.monotonic()

        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=capacity, updated_at=now)
                self._buckets[key] = bucket
            bucket.tokens = min(
                capacity, bucket.tokens + (now - bucket.updated_at) * rate_per_second
            )
            bucket.updated_at = now
            if bucket.tokens < 1.0:
                logger.warning("Rate limit exceeded", extra={"actor_key": key})
                raise TooManyRequestsError(
                    "Too many requests — please slow down and try again shortly."
                )
            bucket.tokens -= 1.0


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
