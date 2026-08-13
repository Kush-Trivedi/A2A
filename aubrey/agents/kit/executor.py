"""The protocol adapter every agent shares: A2A request in, streamed text
out. An agent implements ONE async generator:

    answer_stream(question: str, envelope: ContextEnvelope | None)
        -> AsyncIterator[str]

The kit streams each chunk as an A2A text message on the live context/task.
Failures propagate — the platform's error translator turns them into typed
events; nothing is masked with canned fallback answers."""

from collections.abc import AsyncIterator, Callable

from google.protobuf import json_format

from a2a.helpers import new_task, new_text_status_update_event
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent

from .context_envelope import ContextEnvelope

AnswerStream = Callable[[str, ContextEnvelope | None], AsyncIterator[str]]


class KitAgentExecutor(AgentExecutor):
    def __init__(self, answer_stream: AnswerStream) -> None:
        self._answer_stream = answer_stream

    @staticmethod
    def _envelope_from(context: RequestContext) -> ContextEnvelope | None:
        if context.message is None:
            return None
        metadata = json_format.MessageToDict(context.message.metadata)
        return ContextEnvelope.from_metadata(metadata)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise RuntimeError("Missing task_id or context_id for A2A Streaming")

        await event_queue.enqueue_event(
            new_task(task_id=task_id, context_id=context_id, state=TaskState.TASK_STATE_WORKING)
        )
        async for chunk in self._answer_stream(
            context.get_user_input(), self._envelope_from(context)
        ):
            if not chunk:
                continue
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus.TASK_STATE_WORKING,
                    text=chunk,
                )
            )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(status=TaskStatus.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this agent.")
