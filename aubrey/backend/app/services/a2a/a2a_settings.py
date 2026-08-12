from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context


@dataclass(frozen=True)
class A2ASettings:
    request_timeout_seconds: float
    streaming_enabled: bool
    card_cache_ttl_seconds: float


@lru_cache(maxsize=1)
def get_a2a_settings() -> A2ASettings:
    a2a = get_application_context().agents["a2a"]
    return A2ASettings(
        request_timeout_seconds=float(a2a["request_timeout_seconds"]),
        streaming_enabled=bool(a2a["streaming_enabled"]),
        card_cache_ttl_seconds=float(a2a["card_cache_ttl_seconds"]),
    )
