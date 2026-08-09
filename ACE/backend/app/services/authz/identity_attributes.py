from ...config.application_context import get_application_context
from ...security.jwt_validator import ValidatedIdentity

class IdentityAuthorizationAttributes:
    def __init__(self, claim_names: tuple[str, ...] | None = None) -> None:
        self.claim_names = claim_names or self._configured_claim_names()
    
    def project(self, identity: ValidatedIdentity) -> dict[str, str | int | float | bool]:
        attributes: dict[str, str | int | float | bool] = {}
        for name in self.claim_names:
            value = identity.raw_claims.get(name)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                attributes[name] = value
        return attributes
    
    @staticmethod
    def _configured_claim_names() -> tuple[str, ...]:
        raw = get_application_context().security.get("identity_claim_names") or []
        if isinstance(raw, str):
            raw = raw.split(",")
        return tuple(dict.fromkeys(str(name).strip() for name in raw if str(name).strip()))
