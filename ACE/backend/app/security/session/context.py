from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class SessionContext:
    session_id: str
    tenant_id: str
    user_id: str
    actor_id: str
    email: str
    display_name: str
    auth_provider: str
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    roles: tuple[str, ...] = ()
    user_profile: dict = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.actor_id)
    
    def has_role(self, role: str) -> bool:
        return role in self.roles