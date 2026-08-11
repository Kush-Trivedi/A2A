from ace_agent_kit import ContextEnvelope
from collections.abc import AsyncIterator

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...llm.azure_foundry.ace_azure_foundry import AceAzureFoundry
from ...utils.common.logger import Logger
from ...utils.errors import ValidationError
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service

logger = Logger(__name__).get_logger()


class LlmGatewayService:
    """Service-plane LLM calls on behalf of team agents.

    ACE holds the ONE Foundry integration (yaml base_endpoint/api_key) and
    the key never leaves it. Teams choose model deployments in their
    agent.yaml (`llm:` → registry team_config.llm_deployments); every call
    is validated against that registration — an agent can only use the
    deployments its team registered — and metered per team.
    """

    def __init__(
        self,
        llm: AceAzureFoundry | None = None,
        registry_service: AgentRegistryService | None = None,
    ) -> None:
        self._llm = llm or AceAzureFoundry()
        self._registry = registry_service or get_agent_registry_service()

    def _ensure_foundry_configured(self) -> None:
        foundry = (
            get_application_context().microsoft.get("azure", {}).get("azure_foundry", {})
        )
        for key in ("base_endpoint", "api_key"):
            if not PlaceholderPolicy.is_configured(foundry.get(key)):
                raise ValidationError(
                    f"LLM is not configured. Set microsoft.azure.azure_foundry.{key} in the env yaml."
                )

    async def _ensure_deployment_registered(
        self, *, tenant_id: str, agent_key: str, deployment: str
    ) -> str:
        pair = await self._registry.get_agent_with_team(
            tenant_id=tenant_id, agent_key=agent_key
        )
        if pair is None:
            raise ValidationError(f"Agent '{agent_key}' is not registered.")
        agent, team = pair
        deployments = dict((agent.team_config or {}).get("llm_deployments") or {})
        if deployment not in deployments.values():
            raise ValidationError(
                f"Deployment '{deployment}' is not registered for agent "
                f"'{agent_key}'. Registered: {sorted(deployments.values()) or 'none'}. "
                "Add it to the agent.yaml llm section and re-register."
            )
        return team.key

    async def chat(
        self,
        *,
        envelope: ContextEnvelope,
        agent_key: str,
        deployment: str,
        messages: list[dict[str, str]],
    ) -> str:
        parts: list[str] = []
        async for token in self.stream_chat(
            envelope=envelope,
            agent_key=agent_key,
            deployment=deployment,
            messages=messages,
        ):
            parts.append(token)
        return "".join(parts).strip()

    async def stream_chat(
            self,
            *,
            envelope: ContextEnvelope,
            agent_key: str,
            deployment: str,
            messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        normalized = (deployment or "").strip()
        if not normalized:
            raise ValidationError("Deployment cannot be empty.")
        if not messages:
            raise ValidationError("Messages cannot be empty.")

        team_key = await self._ensure_deployment_registered(
            tenant_id=envelope.tenant_id,
            agent_key=agent_key,
            deployment=normalized,
        )
        self._ensure_foundry_configured()

        answer_char = 0
        async for token in self._llm.astream_chat(messages=messages, model=normalized):
            answer_char += len(token)
            yield token

        logger.info(
            "LLM call metered",
            extra={
                "team_key": team_key,
                "agent_key": agent_key,
                "deployment": normalized,
                "tenant_id": envelope.tenant_id,
                "actor_id": envelope.actor_id,
                "prompt_messages": len(messages),
                "answer_char": answer_char,
            }
        )
        


_service: LlmGatewayService | None = None


def get_llm_gateway_service() -> LlmGatewayService:
    global _service
    if _service is None:
        _service = LlmGatewayService()
    return _service
