# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Who is acting, and where the request came from. Passed to every service function."""

import uuid
from dataclasses import dataclass

from app.platform.permissions.roles import Role

SYSTEM_ROLE = "system"


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID | None
    org_id: uuid.UUID
    role: str
    team_id: uuid.UUID | None
    name: str
    email: str | None = None

    @property
    def is_system(self) -> bool:
        return self.id is None

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def is_privileged(self) -> bool:
        return self.role in (Role.ADMIN, Role.SUPER_ADMIN)


def system_actor(org_id: uuid.UUID, name: str = "System") -> Actor:
    return Actor(id=None, org_id=org_id, role=SYSTEM_ROLE, team_id=None, name=name)


@dataclass(frozen=True)
class RequestInfo:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    origin: str = "screen"
