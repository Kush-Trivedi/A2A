from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context


@dataclass(frozen=True)
class DatabricksSettings:
    host: str
    token: str
    timeout_seconds: int = 300


@lru_cache(maxsize=1)
def get_databricks_settings() -> DatabricksSettings:
    databricks = get_application_context().databricks

    host = str(databricks.get("host") or "").strip().rstrip("/")
    token = str(databricks.get("token") or "").strip()
    if not host:
        raise ValueError("Databricks host is required. Set databricks.host in YAML.")
    if not token:
        raise ValueError("Databricks PAT token is required. Set databricks.token in YAML.")

    try:
        timeout_seconds = int(databricks.get("timeout_seconds") or 300)
    except (TypeError, ValueError):
        timeout_seconds = 300

    return DatabricksSettings(host=host, token=token, timeout_seconds=timeout_seconds)
