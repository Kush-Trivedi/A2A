from functools import lru_cache
from dataclasses import dataclass
from ...config.application_context import get_application_context

@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str | None
    dbname: str
    timeout: int
    auth_mode: str
    ssl_mode: str
    pool_recycle: int
    pool_size: int
    max_overflow: int

@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    db = get_application_context().database["postgres"]
    return DatabaseSettings(
        host=db["host"],
        port=db["port"],
        user=db["user"],
        password=db.get("password"),
        dbname=db["dbname"],
        timeout=db.get("timeout", 30),
        auth_mode=db.get("auth_mode", "password"),
        ssl_mode=db.get("ssl_mode", "prefer"),
        pool_recycle=db.get("pool_recycle", 3600),
        pool_size=int(db.get("pool_size", 10)),
        max_overflow=int(db.get("max_overflow", 20)),
    )