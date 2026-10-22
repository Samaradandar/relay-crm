# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The four roles, scopes and rule enums shared by guard, scope and the reason policy."""

from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    TEAM_LEADER = "team_leader"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class Scope(StrEnum):
    NONE = "none"
    OWN = "own"
    TEAM = "team"
    ALL = "all"


class Mode(StrEnum):
    ALLOW = "allow"
    NEEDS_APPROVAL = "needs_approval"
    REQUEST_ONLY = "request_only"


class ActionClass(StrEnum):
    IMPORTANT = "important"
    ROUTINE = "routine"
    VIEW = "view"
