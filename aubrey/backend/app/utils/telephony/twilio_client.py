"""Generic Twilio Messaging REST client — httpx + stdlib HMAC, no SDK.

Lives in utils because it is pure integration plumbing, reusable by any
channel (SMS today, voice later): send, fetch a message's current status
(callback backfill), and validate webhook signatures. It knows nothing
about campaigns, consent or yaml — callers construct it with plain values.

Signature scheme (Twilio spec): base64(HMAC-SHA1(auth_token,
url + concat(sorted POST params as key+value))) compared against the
X-Twilio-Signature header with a constant-time comparison."""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from ..common.logger import Logger
from ..errors import ExternalServiceError

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
    callers can react (21610 → sync the consent ledger)."""

    def __init__(self, message: str, *, twilio_code: str = "", details: dict | None = None):
        super().__init__(message, details=details)
        self.twilio_code = twilio_code


class TwilioRestClient:
    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        phone_number: str = "",
        messaging_service_sid: str = "",
        timeout_seconds: float = 15.0,
        validate_signatures: bool = True,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._phone_number = phone_number
        self._messaging_service_sid = messaging_service_sid
        self._timeout = timeout_seconds
        self._validate = validate_signatures

    @property
    def configured(self) -> bool:
        return bool(
            self._account_sid and not self._account_sid.startswith("your_")
            and self._auth_token and not self._auth_token.startswith("your_")
        )

    async def send(
        self, *, to: str, body: str, status_callback: str | None = None
    ) -> TwilioSendResult:
        data: dict[str, str] = {"To": to, "Body": body}
        if self._messaging_service_sid and not self._messaging_service_sid.startswith("your_"):
            data["MessagingServiceSid"] = self._messaging_service_sid
        else:
            data["From"] = self._phone_number
        if status_callback:
            data["StatusCallback"] = status_callback

        payload = await self._request(
            "POST", f"/Accounts/{self._account_sid}/Messages.json", data=data, to=to
        )
        return TwilioSendResult(
            sid=str(payload.get("sid") or ""),
            status=str(payload.get("status") or "queued"),
            num_segments=int(payload["num_segments"]) if payload.get("num_segments") else None,
            error_code=str(payload.get("error_code") or "") if payload.get("error_code") else "",
            error_message=str(payload.get("error_message") or "") if payload.get("error_message") else "",
        )

    async def fetch_message(self, *, sid: str) -> dict[str, Any]:
        """Current state of a message straight from Twilio — backfill for
        missed status callbacks. Returns the raw resource dict."""
        return await self._request(
            "GET", f"/Accounts/{self._account_sid}/Messages/{sid}.json"
        )

    async def _request(
        self, method: str, path: str, *, data: dict | None = None, to: str = ""
    ) -> dict[str, Any]:
        url = f"{_API_BASE}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.request(
                    method, url, data=data,
                    auth=(self._account_sid, self._auth_token),
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
                "Twilio API call rejected",
                extra={"to_suffix": to[-4:] if to else "", "twilio_code": code,
                       "http": response.status_code},
            )
            raise SmsSendError(
                f"Twilio rejected the request: {message}",
                twilio_code=code,
                details={"twilio_code": code, "explanation": explain_error_code(code)},
            )
        return payload

    def validate_signature(self, *, url: str, params: dict[str, str], signature: str) -> bool:
        """True when the request provably came from Twilio. When credentials
        are placeholders (local dev), validation is skipped with a warning."""
        if not self._validate:
            return True
        if not self.configured:
            # FAIL CLOSED: an unconfigured channel must never accept
            # unauthenticated webhooks. Local dev opts out explicitly with
            # twilio.validate_signatures: false in yaml.
            logger.error("Twilio not configured — webhook REJECTED (fail closed).")
            return False
        payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        digest = hmac.new(
            self._auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
        ).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature or "")
