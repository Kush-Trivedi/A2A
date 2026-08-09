import asyncio
import inspect
from functools import lru_cache
from typing import Any, Iterable
from casbin import AsyncEnforcer
from ...utils.common.logger import Logger
from casbin_async_sqlalchemy_adapter import Adapter
from .policy_schema import AuthorizationPolicySchema
from ...database.rdbms.pg_session import get_postgres_connector
from .settings import AuthorizationSettings, get_authorization_settings

logger = Logger(__name__).get_logger()

async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class CasbinEnforcer:
    def __init__(self, settings: AuthorizationSettings) -> None:
        self._settings = settings
        self._enforcer: AsyncEnforcer | None = None
        self._initialized = False

    @property
    def enabled(self) -> bool:
        return self._settings.rbac_enabled

    async def initialize(self) -> None:
        if self._initialized:
            return

        if not self._settings.rbac_enabled:
            self._initialized = True
            logger.info("Casbin RBAC disabled — enforce() will allow all requests.")
            return

        engine = get_postgres_connector().engine
        adapter = Adapter(engine=engine)
        await adapter.create_table()
        await AuthorizationPolicySchema.normalize(engine)

        self._enforcer = AsyncEnforcer(self._settings.model_path, adapter)
        await self._enforcer.load_policy()
        added_managed_policies = await self._synchronize_full_access_policies()

        self._initialized = True
        self._start_auto_reload()
        logger.info(
            "[green]Casbin RBAC enabled",
            extra={
                "policies": len(self._enforcer.get_policy()),
                "managed_full_access_roles": list(self._settings.full_access_roles),
                "managed_policies_added": added_managed_policies,
            },
        )


    def _start_auto_reload(self) -> None:
        """Periodic policy reload — keeps every replica's in-memory policies
        fresh without a broker (Service Bus pub/sub can replace this later).
        Disabled when policy_reload_seconds is 0."""
        interval = self._settings.policy_reload_seconds
        if interval <= 0 or getattr(self, "_reload_task", None) is not None:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.reload()
                except Exception:  # noqa: BLE001 — reload failures must not kill the loop
                    logger.warning("Periodic policy reload failed", exc_info=True)

        try:
            self._reload_task = asyncio.get_running_loop().create_task(_loop())
        except RuntimeError:
            self._reload_task = None  # no running loop (scripts) — skip

    async def enforce(self, sub: str, dom: str, obj: str, act: str, attrs: str = "*") -> bool:
        if not self._settings.rbac_enabled:
            return True
        if self._enforcer is None:
            logger.error("Casbin enforce() called before initialize() — denying.")
            return False
        return bool(await _maybe_await(self._enforcer.enforce(sub, dom, obj, act, attrs)))

    async def enforce_any_role(
        self, roles: Iterable[str], dom: str, obj: str, act: str, attrs: str = "*"
    ) -> bool:
        if not self._settings.rbac_enabled:
            return True
        if self._enforcer is None:
            logger.error("Casbin enforce_any_role() called before initialize() — denying.")
            return False

        allowed = False
        for role in roles:
            result, matched_rule = await _maybe_await(
                self._enforcer.enforce_ex(role, dom, obj, act, attrs)
            )
            if len(matched_rule) > 4 and matched_rule[4] == "deny":
                return False
            allowed = allowed or bool(result)
        return allowed


    async def add_policy(
        self,
        sub: str,
        dom: str,
        obj: str,
        act: str,
        eft: str = "allow",
        attrs: str = "*",
    ) -> bool:
        enforcer = self._require_runtime()
        return bool(await _maybe_await(enforcer.add_policy(sub, dom, obj, act, eft, attrs)))

    async def remove_policy(
        self,
        sub: str,
        dom: str,
        obj: str,
        act: str,
        eft: str = "allow",
        attrs: str = "*",
    ) -> bool:
        enforcer = self._require_runtime()
        return bool(await _maybe_await(enforcer.remove_policy(sub, dom, obj, act, eft, attrs)))

    async def add_role_for_user(self, user: str, role: str, dom: str) -> bool:
        enforcer = self._require_runtime()
        return bool(await _maybe_await(enforcer.add_grouping_policy(user, role, dom)))

    async def remove_role_for_user(self, user: str, role: str, dom: str) -> bool:
        enforcer = self._require_runtime()
        return bool(await _maybe_await(enforcer.remove_grouping_policy(user, role, dom)))

    async def reload(self) -> int:
        enforcer = self._require_runtime()
        await _maybe_await(enforcer.load_policy())
        return len(enforcer.get_policy())

    def list_policies(self) -> list[list[str]]:
        if self._enforcer is None:
            return []
        return [list(row) for row in self._enforcer.get_policy()]

    async def close(self) -> None:
        self._enforcer = None
        self._initialized = False


    async def _synchronize_full_access_policies(self) -> int:
        enforcer = self._require_runtime()
        added = 0
        for role in self._settings.full_access_roles:
            was_added = await _maybe_await(
                enforcer.add_policy(role, "*", "*", "*", "allow", "*")
            )
            added += int(bool(was_added))
        return added

    def _require_runtime(self) -> AsyncEnforcer:
        if not self._settings.rbac_enabled:
            raise RuntimeError(
                "Casbin RBAC is disabled — policy mutations are not allowed. "
                "Set ACE_RBAC_ENABLED=true in this environment."
            )
        if self._enforcer is None:
            raise RuntimeError(
                "Casbin enforcer not initialized — call initialize() at app startup."
            )
        return self._enforcer

@lru_cache(maxsize=1)
def get_casbin_enforcer() -> CasbinEnforcer:
    return CasbinEnforcer(get_authorization_settings())
