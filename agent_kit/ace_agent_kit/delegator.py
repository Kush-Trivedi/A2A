from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from a2a.client import (
    AuthInterceptor,
    ClientCallContext,
    ClientConfig,
    CredentialService,
    create_client,
)
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest

from .context_envelope import ContextEnvelope

_WELL_KNOWN_MARKER = "/.well-known/"

BearerTokenProvider = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class DelegationTarget:
    """A partner agent this team delegates a capability to (team-owned config)."""

    capability: str
    card_url: str
    audience: str = ""


@dataclass(frozen=True)
class DelegationResult:
    text: str
    task_id: str = ""
    state: str = ""


class _DelegationCredentialService(CredentialService):
    def __init__(self, provider: BearerTokenProvider, audience: str) -> None:
        self._provider = provider
        self._audience = audience

    async def get_credentials(
        self, security_scheme_name: str, context: ClientCallContext | None
    ) -> str | None:
        if not self._audience:
            return None
        return await self._provider(self._audience)


class AgentDelegator:
    """Cross-team A2A calls with mandatory context forwarding.

    The delegating agent MUST pass the envelope it received (stamped via
    `with_delegation`) and the upstream task id — that is what keeps the
    chain auditable. `bearer_token_provider` supplies service tokens when the
    partner's card declares security.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        bearer_token_provider: BearerTokenProvider | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._token_provider = bearer_token_provider

    @staticmethod
    def _split_card_url(card_url: str) -> tuple[str, str]:
        base, marker, rest = card_url.partition(_WELL_KNOWN_MARKER)
        if marker:
            return base, f"{marker}{rest}"
        return card_url.rstrip("/"), "/.well-known/agent-card.json"

    def _interceptors(self, target: DelegationTarget) -> list[AuthInterceptor]:
        if self._token_provider is None or not target.audience:
            return []
        return [
            AuthInterceptor(
                _DelegationCredentialService(self._token_provider, target.audience)
            )
        ]

    async def delegate(
        self,
        *,
        target: DelegationTarget,
        text: str,
        envelope: ContextEnvelope,
        context_id: str,
        reference_task_ids: tuple[str, ...] = (),
    ) -> DelegationResult:
        message = new_text_message(text, context_id=context_id, role=Role.ROLE_USER)
        message.metadata.update(envelope.to_metadata())
        for task_id in reference_task_ids:
            message.reference_task_ids.append(task_id)
        request = SendMessageRequest(message=message)

        base_url, relative_card_path = self._split_card_url(target.card_url)
        texts: list[str] = []
        task_id = ""
        state = ""

        httpx_client = httpx.AsyncClient(timeout=self._timeout_seconds)
        client = None
        try:
            client = await create_client(
                base_url,
                ClientConfig(streaming=True, httpx_client=httpx_client),
                interceptors=self._interceptors(target),
                relative_card_path=relative_card_path,
            )
            async for response in client.send_message(request):
                payload = response.WhichOneof("payload")
                if payload == "message":
                    part_text = get_message_text(response.message)
                    if part_text:
                        texts.append(part_text)
                elif payload == "status_update":
                    update = response.status_update
                    task_id = update.task_id or task_id
                    state = str(update.status.state)
                    if update.status.HasField("message"):
                        status_text = get_message_text(update.status.message)
                        if status_text:
                            texts.append(status_text)
                elif payload == "task":
                    task_id = response.task.id or task_id
                    state = str(response.task.status.state)
                    if response.task.status.HasField("message"):
                        status_text = get_message_text(response.task.status.message)
                        if status_text:
                            texts.append(status_text)
        finally:
            if client is not None:
                await client.close()
            await httpx_client.aclose()

        return DelegationResult(
            text="\n".join(dict.fromkeys(texts)).strip(),
            task_id=task_id,
            state=state,
        )
