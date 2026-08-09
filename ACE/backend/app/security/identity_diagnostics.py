import json
from ..config.application_context import get_application_context
from ..utils.common.logger import Logger
from .jwt_validator import ValidatedIdentity

logger = Logger(__name__).get_logger()


class IdentityClaimDiagnostics:
    SAFE_CLAIMS = (
        "tid",
        "oid",
        "sub",
        "preferred_username",
        "email",
        "name",
        "given_name",
        "family_name",
        "roles",
        "groups",
        "department",
        "jobTitle",
        "employeeType",
    )

    @classmethod
    def log(cls, identity: ValidatedIdentity) -> None:
        if not bool(get_application_context().security.get("log_identity_claims", False)):
            return

        claims = {
            name: identity.raw_claims[name]
            for name in cls.SAFE_CLAIMS
            if name in identity.raw_claims
        }

        logger.info(
            "Validated Entra identity payload (raw JWT and signature omitted): %s",
            json.dumps(claims, sort_keys=True, default=str),
        )
