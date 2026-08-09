from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy


@dataclass(frozen=True)
class TwilioSettings:
    account_sid: str
    auth_token: str
    messaging_service_sid: str
    webhook_base_url: str
    timeout_seconds: int
    inbound_number: str
    outbound_number: str
    default_agent: str
    inbound_roles: tuple[str, ...]
    opt_out_keywords: tuple[str, ...]
    opt_in_keywords: tuple[str, ...]

    @property
    def is_configured(self) -> bool:
        return PlaceholderPolicy.is_configured(self.account_sid) and (
            PlaceholderPolicy.is_configured(self.auth_token)
        )


@lru_cache(maxsize=1)
def get_twilio_settings() -> TwilioSettings:
    cfg = get_application_context().twilio
    messaging = cfg.get("messaging", {}) or {}
    return TwilioSettings(
        account_sid=str(cfg.get("account_sid") or ""),
        auth_token=str(cfg.get("auth_token") or ""),
        messaging_service_sid=str(cfg.get("messaging_service_sid") or ""),
        webhook_base_url=str(cfg.get("webhook_base_url") or "").rstrip("/"),
        timeout_seconds=int(cfg.get("timeout_seconds") or 30),
        inbound_number=str((cfg.get("inbound") or {}).get("phone_number") or ""),
        outbound_number=str((cfg.get("outbound") or {}).get("phone_number") or ""),
        default_agent=str(messaging.get("default_agent") or "general"),
        inbound_roles=tuple(messaging.get("inbound_roles") or ["sms_patient"]),
        opt_out_keywords=tuple(
            str(k).lower() for k in messaging.get("opt_out_keywords")
            or ["stop", "stopall", "unsubscribe", "quit", "optout", "opt out"]
        ),
        opt_in_keywords=tuple(
            str(k).lower() for k in messaging.get("opt_in_keywords")
            or ["start", "unstop", "yes start"]
        ),
    )
