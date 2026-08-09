from dataclasses import dataclass, field

from a2a.types import TaskState

from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()

_STATE_NAMES: dict[int, str] = {
    TaskState.TASK_STATE_UNSPECIFIED: "unknown",
    TaskState.TASK_STATE_SUBMITTED: "submitted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_AUTH_REQUIRED: "auth_required",
}

_TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


@dataclass
class TaskLifecycleTracker:
    """Tracks one A2A task's state transitions during a chat turn."""

    agent_key: str
    task_id: str = ""
    transitions: list[str] = field(default_factory=list)

    def record_task_id(self, task_id: str) -> None:
        if task_id and not self.task_id:
            self.task_id = task_id

    def record(self, state: int, *, task_id: str = "") -> None:
        if task_id:
            self.task_id = task_id
        name = _STATE_NAMES.get(state, "unknown")
        if not self.transitions or self.transitions[-1] != name:
            self.transitions.append(name)
            logger.info(
                "A2A task state transition",
                extra={
                    "agent_key": self.agent_key,
                    "task_id": self.task_id,
                    "state": name,
                },
            )

    @property
    def current_state(self) -> str:
        return self.transitions[-1] if self.transitions else "unknown"

    @property
    def is_terminal(self) -> bool:
        return self.current_state in {"completed", "failed", "canceled", "rejected"}

    @property
    def needs_user_action(self) -> bool:
        return self.current_state in {"input_required", "auth_required"}

    def summary(self) -> dict[str, object]:
        return {
            "agent_key": self.agent_key,
            "task_id": self.task_id,
            "transitions": list(self.transitions),
            "final_state": self.current_state,
        }

    @staticmethod
    def state_name(state: int) -> str:
        return _STATE_NAMES.get(state, "unknown")

    @staticmethod
    def is_terminal_state(state: int) -> bool:
        return state in _TERMINAL_STATES
