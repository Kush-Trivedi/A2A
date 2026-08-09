from dataclasses import replace
from .jwt_validator import ValidatedIdentity
from .oauth_client import EntraUserProfile


class IdentityProfileEnricher:
    @staticmethod
    def enrich(
        identity: ValidatedIdentity,
        profile: EntraUserProfile | None,
    ) -> ValidatedIdentity:
        if profile is None:
            return identity
        if not profile.actor_id or profile.actor_id.casefold() != identity.actor_id.casefold():
            return identity

        return replace(
            identity,
            email=profile.email or identity.email,
            first_name=profile.first_name or identity.first_name,
            last_name=profile.last_name or identity.last_name,
            display_name=profile.display_name or identity.display_name,
        )
