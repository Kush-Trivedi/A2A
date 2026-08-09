import asyncio

from twilio.request_validator import RequestValidator
from twilio.rest import Client

from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from .twilio_settings import TwilioSettings, get_twilio_settings

logger = Logger(__name__).get_logger()


class TwilioSmsClient:
    """The one Twilio integration (yaml-driven). Teams never hold Twilio
    credentials — they send through ACE. Blocking SDK runs in a worker thread."""

    def __init__(self, settings: TwilioSettings | None = None) -> None:
        self._settings = settings or get_twilio_settings()
        self._client: Client | None = None
        self._validator: RequestValidator | None = None

    def _ensure_configured(self) -> None:
        if not self._settings.is_configured:
            raise ValidationError(
                "Twilio is not configured. Set twilio.account_sid and "
                "twilio.auth_token in the env yaml."
            )

    def _rest(self) -> Client:
        self._ensure_configured()
        if self._client is None:
            self._client = Client(self._settings.account_sid, self._settings.auth_token)
        return self._client

    async def send_text_message(
        self, *, to_number: str, body: str, status_callback: str | None = None
    ) -> str:
        def _send() -> str:
            kwargs: dict = {"to": to_number, "body": body}
            if self._settings.messaging_service_sid and not self._settings.messaging_service_sid.startswith("your_"):
                kwargs["messaging_service_sid"] = self._settings.messaging_service_sid
            else:
                kwargs["from_"] = self._settings.outbound_number
            if status_callback:
                kwargs["status_callback"] = status_callback
            message = self._rest().messages.create(**kwargs)
            return str(message.sid)

        try:
            return await asyncio.to_thread(_send)
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "SMS send failed — check twilio.* settings.",
                code="sms_send_failed",
                cause=exc,
            ) from exc

    def validate_signature(
        self, *, url: str, params: dict, signature: str
    ) -> bool:
        """Twilio webhook signature check; refuses open webhooks when
        credentials are configured, allows local dev when they are not."""
        if not self._settings.is_configured:
            logger.warning("Twilio not configured — webhook signature check skipped (dev only).")
            return True
        if self._validator is None:
            self._validator = RequestValidator(self._settings.auth_token)
        return bool(self._validator.validate(url, params, signature))


_client: TwilioSmsClient | None = None


def get_twilio_sms_client() -> TwilioSmsClient:
    global _client
    if _client is None:
        _client = TwilioSmsClient()
    return _client
