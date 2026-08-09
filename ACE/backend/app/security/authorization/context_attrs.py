from ..session import SessionContext

class AuthorizationContextBuilder:
    @classmethod
    def build(cls, context: SessionContext) -> str:
        parts = [
            "tenant",
            cls._clean_token(context.tenant_id),
            "user",
            cls._clean_token(context.user_id),
        ]

        profile = context.user_profile or {}
        for key in sorted(profile.keys()):
            value = profile.get(key)
            if isinstance(value, (str, int, float, bool)):
                parts.extend(
                    ["attr", cls._clean_token(key), cls._clean_token(str(value))]
                )
        return "/" + "/".join(parts)
    
    @staticmethod
    def _clean_token(value: object) -> str:
        text = str(value).strip().lower()
        if not text:
            return "na"
        normalized = "".join(
            ch if ch.isalnum() or ch in "-_" else "-" for ch in text
        ).strip("-")
        return normalized or "na"