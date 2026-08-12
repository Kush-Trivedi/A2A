"""The conversation window agents receive each turn.

ACE persisted history but sent agents only the current question — multi-turn
was an illusion. Here every dispatch carries a token-budgeted window: walk
the session's messages newest-first, keep whole messages while they fit the
budget (tiktoken-measured), return them in chronological order. The budget
comes from yaml agents.memory.window_tokens; a team manifest can lower it
per agent later."""

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from ...config.application_context import get_application_context
from ...entity.chat import ChatMessageEntity, MessageKind, MessageRole


@dataclass(frozen=True)
class WindowMessage:
    role: str  # user | assistant
    content: str


@lru_cache(maxsize=1)
def default_window_tokens() -> int:
    return int(get_application_context().agents["memory"]["window_tokens"])


class MemoryWindowBuilder:
    def __init__(self, *, encoding_name: str = "cl100k_base") -> None:
        self._encoding = tiktoken.get_encoding(encoding_name)

    def build(
        self,
        messages: list[ChatMessageEntity],
        *,
        window_tokens: int | None = None,
    ) -> list[WindowMessage]:
        budget = window_tokens if window_tokens is not None else default_window_tokens()
        if budget <= 0:
            return []

        window: list[WindowMessage] = []
        used = 0
        for message in reversed(messages):
            if not self._belongs_in_window(message):
                continue
            cost = len(self._encoding.encode(message.content))
            if used + cost > budget and window:
                break
            window.append(WindowMessage(role=message.role, content=message.content))
            used += cost
        window.reverse()
        return window

    @staticmethod
    def _belongs_in_window(message: ChatMessageEntity) -> bool:
        """Only real conversation: user turns and agent ANSWERS. Routing
        artifacts (disambiguation prompts, refusals) are UI history, not
        context the next agent should reason over."""
        if message.role == MessageRole.USER:
            return True
        if message.role != MessageRole.ASSISTANT:
            return False
        return (message.message_metadata or {}).get("kind") == MessageKind.ANSWER


_builder: MemoryWindowBuilder | None = None


def get_memory_window_builder() -> MemoryWindowBuilder:
    global _builder
    if _builder is None:
        _builder = MemoryWindowBuilder()
    return _builder
