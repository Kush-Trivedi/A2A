import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import yaml
from ...config.application_context import ApplicationContext, get_application_context
from ...entity.agents.registered_agent_entity import AgentStatus
from ...security.session import SessionContext
from ...security.settings import AuthSettings, get_auth_settings
from ...utils.common.logger import Logger
from .registry_service import AgentRegistryService, get_agent_registry_service

logger = Logger(__name__).get_logger()

class AgentBootstrapService:
    def __init__(
        self,
        context: ApplicationContext | None = None,
        auth_settings: AuthSettings | None = None,
        registry: AgentRegistryService | None = None,
    ) -> None:
        self._context = context or get_application_context()
        self._auth = auth_settings or get_auth_settings()
        self._registry = registry or get_agent_registry_service()

    @property
    def configured(self) -> bool:
        return bool(self._definitions())

    async def reconcile_until_ready(self) -> None:
        definitions = self._definitions()
        if not definitions:
            return

        retry_seconds = max(
            1, int(self._context.agents.get("bootstrap_retry_seconds") or 5)
        )
        pending = definitions
        while pending:
            pending = await self._reconcile(pending)
            if pending:
                logger.warning(
                    "Configured A2A agents are not reachable; registration will retry",
                    extra={
                        "agent_keys": [str(item.get("agent_key", "")) for item in pending],
                        "retry_seconds": retry_seconds,
                    },
                )
                await asyncio.sleep(retry_seconds)

    def _definitions(self) -> list[dict[str, Any]]:
        raw = self._context.agents.get("bootstrap") or []
        if not isinstance(raw, list):
            raise ValueError("agents.bootstrap must be a list.")
        definitions: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            configured = dict(item)
            manifest_path = str(configured.pop("manifest_path", "") or "").strip()
            if not manifest_path:
                definitions.append(configured)
                continue
            path = Path(manifest_path).expanduser()
            if not path.is_absolute():
                path = (Path(__file__).resolve().parents[4] / path).resolve()
            definitions.append({**self._from_manifest(path), **configured})
        return definitions

    @staticmethod
    def _from_manifest(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8-sig") as file:
            manifest = yaml.safe_load(file) or {}
        agent = dict(manifest.get("agent") or {})
        ace = dict(manifest.get("ace") or {})
        llm = dict(manifest.get("llm") or {})
        deployments = dict(llm.get("deployments") or {})
        data = dict(manifest.get("data") or {})
        data["llm_deployments"] = deployments
        public_url = str(ace.get("public_url") or "").rstrip("/")
        return {
            "team_key": agent.get("team_key"),
            "agent_key": agent.get("agent_key"),
            "display_name": agent.get("display_name"),
            "description": agent.get("description", ""),
            "version": agent.get("version", "0.1.0"),
            "card_url": f"{public_url}/.well-known/agent-card.json",
            "permission": ace.get("permission", "chat"),
            "allowed_roles": ace.get("allowed_roles", []),
            "knowledge_sources": ace.get("knowledge_sources", []),
            "retrieval_mode": ace.get("retrieval_mode"),
            "team_config": data,
            "prompts": manifest.get("prompts", {}),
        }

    async def _reconcile(
        self, definitions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for definition in definitions:
            try:
                await self._register_if_missing(definition)
            except Exception as exc:
                pending.append(definition)
                logger.warning(
                    "Configured A2A agent registration failed",
                    extra={
                        "agent_key": str(definition.get("agent_key", "")),
                        "error": str(exc),
                    },
                )
        return pending

    async def _register_if_missing(self, definition: dict[str, Any]) -> None:
        required = ("team_key", "agent_key", "display_name", "card_url")
        missing = [key for key in required if not str(definition.get(key, "")).strip()]
        if missing:
            raise ValueError(
                f"Configured agent is missing required fields: {', '.join(missing)}"
            )

        tenant_id = str(definition.get("tenant_id") or self._auth.tenant).strip()
        agent_key = str(definition["agent_key"]).strip()
        context = self._system_context(tenant_id)
        team_key = str(definition["team_key"]).strip()
        display_name = str(definition["display_name"]).strip()
        await self._registry.register_team(
            context=context,
            key=team_key,
            name=str(definition.get("team_name") or team_key.replace("_", " ").title()),
            description=str(
                definition.get("team_description")
                or f"ODT team owning the {display_name}."
            ),
            contact_email=(
                str(definition["contact_email"])
                if definition.get("contact_email")
                else None
            ),
        )
        agent, _, policies_seeded = await self._registry.register_agent(
            context=context,
            team_key=team_key,
            agent_key=agent_key,
            display_name=display_name,
            description=str(definition.get("description") or ""),
            card_url=str(definition["card_url"]).strip(),
            version=str(definition.get("version") or "0.1.0"),
            permission=str(definition.get("permission") or "chat"),
            allowed_roles=self._strings(definition.get("allowed_roles")),
            aliases=self._strings(definition.get("aliases")),
            knowledge_sources=self._strings(definition.get("knowledge_sources")),
            retrieval_mode=(
                str(definition["retrieval_mode"])
                if definition.get("retrieval_mode")
                else None
            ),
            team_config=dict(definition.get("team_config") or {}),
            prompts=dict(definition.get("prompts") or {}),
        )
        await self._registry.set_agent_status(
            context=context, agent_key=agent.agent_key, status=AgentStatus.ACTIVE
        )
        logger.info(
            "Configured A2A agent registered and activated",
            extra={
                "tenant_id": tenant_id,
                "agent_key": agent.agent_key,
                "policies_seeded": policies_seeded,
            },
        )

    @staticmethod
    def _strings(raw: object) -> list[str]:
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(value).strip() for value in raw if str(value).strip()]

    @staticmethod
    def _system_context(tenant_id: str) -> SessionContext:
        now = datetime.now(timezone.utc)
        return SessionContext(
            session_id="agent-bootstrap",
            tenant_id=tenant_id,
            user_id="agent-bootstrap",
            actor_id="agent-bootstrap",
            email="",
            display_name="Agent Bootstrap",
            auth_provider="system",
            csrf_token="",
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(minutes=5),
            roles=(),
        )


_service: AgentBootstrapService | None = None


def get_agent_bootstrap_service() -> AgentBootstrapService:
    global _service
    if _service is None:
        _service = AgentBootstrapService()
    return _service