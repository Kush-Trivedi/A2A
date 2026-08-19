"""The append+decay half of the memory contract (NEW_PLAN §5): wrong or
stale memories FADE — weights age toward the floor and are pruned — they
are never LLM-edited in place. One background loop in the app lifespan
runs every layer's decay(), the browser-session purge, AND the retention
service's policy sweeps (M10-S2 — archived-session purge, SMS body
overwrite, genie TTL, external memory countdown) on the yaml interval
(agents.memory.decay_interval_seconds).

Each layer's decay recomputes weight from record age, and the retention
sweeps re-derive their cutoffs from the clock, so the whole pass is
idempotent — replicas all running it, or a crashed run retried, converge
instead of compounding. Failures log and the loop lives on; stop()
cancels cleanly on shutdown."""

import asyncio

from ...security.session import get_session_store
from ...utils.common.logger import Logger
from .orchestrator import MemoryOrchestrator, get_memory_orchestrator
from .settings import MemorySettings, get_memory_settings

logger = Logger(__name__).get_logger()


class MemoryDecayScheduler:
    def __init__(
        self,
        settings: MemorySettings | None = None,
        orchestrator: MemoryOrchestrator | None = None,
    ) -> None:
        self._settings = settings or get_memory_settings()
        self._orchestrator = orchestrator or get_memory_orchestrator()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="memory-decay")
        logger.info(
            "Memory decay scheduler started",
            extra={"interval_seconds": self._settings.decay_interval_seconds},
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.decay_interval_seconds)
            await self.run_once()

    async def run_once(self) -> None:
        for name, layer in self._orchestrator.layers.items():
            try:
                pruned = await layer.decay()
                logger.info(
                    "Memory decay pass", extra={"layer": name, "pruned": pruned}
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one layer never stops the sweep
                logger.error("Memory decay failed", extra={"layer": name}, exc_info=True)
        try:
            purged = await get_session_store().purge_expired()
            logger.info("Expired browser sessions purged", extra={"purged": purged})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.error("Browser session purge failed", exc_info=True)
        # M10-S2: retention rides the same interval — lazy import keeps the
        # memory package free of a hard dependency on services.retention.
        try:
            from ..retention.retention_service import get_retention_service

            results = await get_retention_service().run_once()
            logger.info("Retention pass", extra={"results": results})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.error("Retention pass failed", exc_info=True)


_scheduler: MemoryDecayScheduler | None = None


def get_memory_decay_scheduler() -> MemoryDecayScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MemoryDecayScheduler()
    return _scheduler
