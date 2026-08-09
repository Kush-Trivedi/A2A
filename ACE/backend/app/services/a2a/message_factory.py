from ace_agent_kit import ContextEnvelope

from a2a.helpers import new_text_message
from a2a.types import Message, Role, SendMessageRequest


class A2AMessageFactory:
    """The only place ACE builds outbound A2A protocol messages.

    `context_id` carries the ACE chat session id so one conversation thread
    spans every agent involved. The ContextEnvelope rides in message metadata
    under the shared kit namespace — the bearer token says which service
    calls, the envelope says on whose behalf.
    """

    def user_message(
        self,
        *,
        text: str,
        context_id: str,
        envelope: ContextEnvelope | None = None,
        reference_task_ids: tuple[str, ...] = (),
    ) -> Message:
        message = new_text_message(
            text,
            context_id=context_id,
            role=Role.ROLE_USER,
        )
        if envelope is not None:
            message.metadata.update(envelope.to_metadata())
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
