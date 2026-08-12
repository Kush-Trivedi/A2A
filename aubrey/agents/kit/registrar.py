"""Startup self-registration with aubrey — bounded retry, idempotent, so
aubrey and the agent can start in any order. The manifest is the source of
truth: key, skills (the routing surface), card_url, roles."""

import asyncio

from .capability_client import AubreyCapabilityClient
from .config import AgentConfig

_RETRY_SECONDS = 3.0
_MAX_ATTEMPTS = 40


class AgentRegistrar:
    def __init__(self, config: AgentConfig, client: AubreyCapabilityClient) -> None:
        self._config = config
        self._client = client

    def payload(self) -> dict:
        return {
            "team_key": self._config.team_key,
            "agent_key": self._config.agent_key,
            "display_name": self._config.display_name,
            "description": self._config.description,
            "card_url": self._config.card_url,
            "version": self._config.version,
            "permission": self._config.permission,
            "allowed_roles": list(self._config.allowed_roles),
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "examples": list(s.examples),
                }
                for s in self._config.skills
            ],
        }

    async def register_with_retry(self) -> None:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                await self._client.register_self(self.payload())
                print(
                    f"[{self._config.agent_key}] registered with aubrey "
                    f"(attempt {attempt})",
                    flush=True,
                )
                return
            except Exception as exc:  # noqa: BLE001 — aubrey may not be up yet
                print(
                    f"[{self._config.agent_key}] registration attempt {attempt} "
                    f"failed: {exc}",
                    flush=True,
                )
                await asyncio.sleep(_RETRY_SECONDS)
        print(
            f"[{self._config.agent_key}] gave up self-registering after "
            f"{_MAX_ATTEMPTS} attempts — register manually or restart",
            flush=True,
        )
