"""Raw Twilio Messaging REST client — httpx + stdlib HMAC, no SDK
dependency. Two jobs: send a message, validate a webhook signature.

Signature scheme (Twilio spec): base64(HMAC-SHA1(auth_token,
url + concat(sorted POST params as key+value))) compared against the
X-Twilio-Signature header with a constant-time comparison."""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError
from .sms_settings import SmsSettings, get_sms_settings

logger = Logger(__name__).get_logger()

_API_BASE = "https://api.twilio.com/2010-04-01"

# Human explanations for the codes that actually show up in practice —
# stored on the message row so failures are self-describing in the DB.
TWILIO_ERROR_CODES: dict[str, str] = {
    "21211": "Invalid 'To' phone number.",
    "21408": "Permission to send to this region is not enabled on the account.",
    "21610": "Recipient has opted out (Twilio block list). Reply START re-enables.",
    "21614": "'To' number is not a valid mobile number.",
    "30003": "Unreachable handset — powered off, airplane mode, or out of range.",
    "30004": "Blocked by the carrier or the destination device.",
    "30005": "Unknown or inactive destination number.",
    "30006": "Landline or unreachable carrier.",
    "30007": "Filtered by the carrier (flagged as spam).",
    "30008": "Unknown delivery error at the carrier.",
    "30034": "Sender is a US 10DLC number not registered to an approved A2P campaign.",
    "63038": "Account daily message limit reached.",
}


def explain_error_code(code: str) -> str:
    return TWILIO_ERROR_CODES.get(str(code or "").strip(), "")


@dataclass(frozen=True)
class TwilioSendResult:
    sid: str
    status: str
    num_segments: int | None
    error_code: str
    error_message: str


class SmsSendError(ExternalServiceError):
    """A send the Twilio API rejected — carries the Twilio error code so
    callers can react (21610 → sync our consent ledger)."""

    def __init__(self, message: str, *, twilio_code: str = "", details: dict | None = None):
        super().__init__(message, details=details)
        self.twilio_code = twilio_code


class TwilioRestClient:
    def __init__(self, settings: SmsSettings | None = None) -> None:
        self._settings = settings or get_sms_settings()

    async def send(
        self, *, to: str, body: str, status_callback: str | None = None
    ) -> TwilioSendResult:
        self._settings.require_configured()
        data: dict[str, str] = {"To": to, "Body": body}
        if self._settings.messaging_service_sid and not self._settings.messaging_service_sid.startswith("your_"):
            data["MessagingServiceSid"] = self._settings.messaging_service_sid
        else:
            data["From"] = self._settings.phone_number
        if status_callback:
            data["StatusCallback"] = status_callback

        url = f"{_API_BASE}/Accounts/{self._settings.account_sid}/Messages.json"
        async with httpx.AsyncClient(timeout=self._settings.timeout_seconds) as client:
            try:
                response = await client.post(
                    url,
                    data=data,
                    auth=(self._settings.account_sid, self._settings.auth_token),
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    "The Twilio API could not be reached.", cause=exc
                ) from exc

        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 — non-JSON error body
            pass

        if response.status_code >= 400:
            code = str(payload.get("code") or "")
            message = str(payload.get("message") or response.reason_phrase)
            logger.error(
                "Twilio send rejected",
                extra={"to_suffix": to[-4:], "twilio_code": code, "http": response.status_code},
            )
            raise SmsSendError(
                f"Twilio rejected the message: {message}",
                twilio_code=code,
                details={"twilio_code": code, "explanation": explain_error_code(code)},
            )

        return TwilioSendResult(
            sid=str(payload.get("sid") or ""),
            status=str(payload.get("status") or "queued"),
            num_segments=int(payload["num_segments"]) if payload.get("num_segments") else None,
            error_code=str(payload.get("error_code") or "") if payload.get("error_code") else "",
            error_message=str(payload.get("error_message") or "") if payload.get("error_message") else "",
        )

    def validate_signature(self, *, url: str, params: dict[str, str], signature: str) -> bool:
        """True when the request provably came from Twilio. When the channel
        is unconfigured (local dev), validation is skipped with a warning —
        same policy as the legacy platform."""
        if not self._settings.validate_signatures:
            return True
        if not self._settings.auth_token or self._settings.auth_token.startswith("your_"):
            logger.warning("Twilio not configured — webhook signature check skipped (dev only).")
            return True
        payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        digest = hmac.new(
            self._settings.auth_token.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature or "")


_client: TwilioRestClient | None = None


def get_twilio_rest_client() -> TwilioRestClient:
    global _client
    if _client is None:
        _client = TwilioRestClient()
    return _client
