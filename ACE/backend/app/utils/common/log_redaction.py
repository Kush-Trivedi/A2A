import logging
import re
from collections.abc import Mapping
from typing import Iterable

_SENSITIVE_KEYS: tuple[str, ...] = (
    "code",
    "state",
    "session_state",
    "id_token",
    "access_token",
    "refresh_token",
    "token",
    "client_secret",
    "secret",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "sig",
    "signature",
    "assertion",
    "client_assertion",
)

_REDACTED = "REDACTED"
_QUERY_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(key) for key in _SENSITIVE_KEYS) + r")\s*=\s*([^&\s]+)"
)

def redact_query_string(text: str) -> str:
    if not text or "=" not in text:
        return text
    return _QUERY_PATTERN.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)

class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_query_string(arg) if isinstance(arg, str) else arg for arg in record.args
                )
            elif isinstance(record.args, Mapping):
                record.args = {
                    key: redact_query_string(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
        elif isinstance(record.msg, str):
            record.msg = redact_query_string(record.msg)
        return True
    
_TARGET_LOGGERS: tuple[str, ...] = ("uvicorn.access","uvicorn", "gunicorn.access")

def install_log_redaction(logger_names: Iterable[str] = _TARGET_LOGGERS) -> None:
    log_filter = SensitiveDataFilter()
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        if not any(isinstance(f, SensitiveDataFilter) for f in logger.filters):
            logger.addFilter(log_filter)