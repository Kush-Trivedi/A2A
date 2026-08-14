"""Factory wiring the generic utils Twilio client to the env yaml.

utils/telephony owns the integration mechanics; this module is the only
place that binds it to configuration — services and routes ask here.
Construction never requires credentials (webhook signature checks must
work, and dev-skip gracefully, before creds are filled in) — senders call
settings.require_configured() before dispatching."""

from ...utils.telephony import TwilioRestClient
from .sms_settings import get_sms_settings

_client: TwilioRestClient | None = None


def get_twilio_rest_client() -> TwilioRestClient:
    global _client
    if _client is None:
        settings = get_sms_settings()
        _client = TwilioRestClient(
            account_sid=settings.account_sid,
            auth_token=settings.auth_token,
            phone_number=settings.phone_number,
            messaging_service_sid=settings.messaging_service_sid,
            timeout_seconds=settings.timeout_seconds,
            validate_signatures=settings.validate_signatures,
        )
    return _client
