"""SMS channel settings — the twilio section of the env yaml. Same
philosophy as everything else: one code path in every environment, and the
PlaceholderPolicy names the exact key to fill when credentials are absent."""

from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...utils.errors import ValidationError


@dataclass(frozen=True)
class SmsSettings:
    account_sid: str
    auth_token: str
    phone_number: str            # platform number, E.164
    messaging_service_sid: str   # preferred over phone_number when set
    webhook_base_url: str        # public base URL used for signature validation
    validate_signatures: bool
    timeout_seconds: float
    tenant_id: str               # tenant that owns SMS-channel data
    user_role: str               # role granted to phone-derived users
    default_campaign: str        # campaign for cold inbound texts ("" = store only)
    max_length: int              # outbound body cap (characters)
    window_tokens: int           # memory window budget for SMS threads
    status_callbacks_enabled: bool

    @property
    def sender_configured(self) -> bool:
        return PlaceholderPolicy.is_configured(
            self.messaging_service_sid
        ) or PlaceholderPolicy.is_configured(self.phone_number)

    def require_configured(self) -> None:
        checks = {
            "account_sid": self.account_sid,
            "auth_token": self.auth_token,
        }
        for key, value in checks.items():
            if not PlaceholderPolicy.is_configured(value):
                raise ValidationError(
                    f"The SMS channel is not configured. Set twilio.{key} "
                    "in the env yaml."
                )
        if not self.sender_configured:
            raise ValidationError(
                "The SMS channel has no sender. Set twilio.phone_number or "
                "twilio.messaging_service_sid in the env yaml."
            )


@lru_cache(maxsize=1)
def get_sms_settings() -> SmsSettings:
    twilio = get_application_context().twilio
    sms = twilio.get("sms") or {}
    return SmsSettings(
        account_sid=str(twilio.get("account_sid") or ""),
        auth_token=str(twilio.get("auth_token") or ""),
        phone_number=str(twilio.get("phone_number") or ""),
        messaging_service_sid=str(twilio.get("messaging_service_sid") or ""),
        webhook_base_url=str(twilio.get("webhook_base_url") or "").rstrip("/"),
        validate_signatures=bool(twilio.get("validate_signatures", True)),
        timeout_seconds=float(twilio.get("timeout_seconds") or 15),
        tenant_id=str(twilio.get("tenant_id") or "default"),
        user_role=str(sms.get("user_role") or "sms_user"),
        default_campaign=str(sms.get("default_campaign") or ""),
        max_length=int(sms.get("max_length") or 480),
        window_tokens=int(sms.get("window_tokens") or 600),
        status_callbacks_enabled=bool(sms.get("status_callbacks_enabled", True)),
    )
