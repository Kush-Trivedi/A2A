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
    agents = get_application_context().agents

    try:
        timeout = float(agents.get("a2a_request_timeout_seconds") or 120)
    except (TypeError, ValueError):
        timeout = 120.0

    streaming = agents.get("a2a_streaming_enabled")
    streaming_enabled = True if streaming is None else bool(streaming)

    try:
        card_ttl = float(agents.get("card_cache_ttl_seconds") or 300)
    except (TypeError, ValueError):
        card_ttl = 300.0

    return A2ASettings(
        request_timeout_seconds=timeout,
        streaming_enabled=streaming_enabled,
        card_cache_ttl_seconds=card_ttl,
    )
