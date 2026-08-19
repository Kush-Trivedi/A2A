from a2a.helpers import new_text_message
from a2a.types import Message, Role, SendMessageRequest

from .context_envelope import ENVELOPE_NAMESPACE, ContextEnvelope
from .envelope_signer import EnvelopeSigner, get_envelope_signer


class A2AMessageFactory:
    """The only place aubrey builds outbound A2A protocol messages.

    `context_id` carries the chat session id so one conversation thread
    spans every agent involved. The ContextEnvelope rides in message
    metadata — the bearer token says which service calls, the envelope says
    on whose behalf and with what conversation window.

    Every outbound envelope is platform-signed here (M10-S3): the signer
    adds {"sig", "issued_at"} over the identity fields, which capability
    endpoints verify — agents pass the fields through verbatim and cannot
    mint or alter identities. With no signing key configured the envelope
    goes out unsigned (dev passthrough)."""

    def __init__(self, signer: EnvelopeSigner | None = None) -> None:
        # Lazy default — tests can inject; production resolves the
        # yaml-configured singleton on first use, not at import.
        self._signer = signer

    def _get_signer(self) -> EnvelopeSigner:
        if self._signer is None:
            self._signer = get_envelope_signer()
        return self._signer

    def user_message(
        self,
        *,
        text: str,
        context_id: str,
        envelope: ContextEnvelope | None = None,
        reference_task_ids: tuple[str, ...] = (),
    ) -> Message:
        message = new_text_message(text, context_id=context_id, role=Role.ROLE_USER)
        if envelope is not None:
            metadata = envelope.to_metadata()
            payload = metadata.get(ENVELOPE_NAMESPACE)
            if isinstance(payload, dict):
                metadata[ENVELOPE_NAMESPACE] = self._get_signer().sign(payload)
            message.metadata.update(metadata)
        for task_id in reference_task_ids:
            message.reference_task_ids.append(task_id)
        return message

    def send_request(
        self,
        *,
        text: str,
        context_id: str,
        envelope: ContextEnvelope | None = None,
        reference_task_ids: tuple[str, ...] = (),
    ) -> SendMessageRequest:
        return SendMessageRequest(
            message=self.user_message(
                text=text,
                context_id=context_id,
                envelope=envelope,
                reference_task_ids=reference_task_ids,
            )
        )
