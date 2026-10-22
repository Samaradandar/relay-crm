# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Reason required for User, TL, Admin on important actions; optional for Super admin (PRD 4.2).

| class     | User | Team Leader | Admin    | Super admin |
| important | req  | req         | req      | optional    |
| routine   | none | none        | req      | optional    |
| view      | none | none        | none     | none        |

Any non-empty text is accepted (no minimum length). Whitespace-only counts as empty.

A few PRD rows say a reason is always required, with no Super-admin exception (Phase 5's
`renewal_cancellation_churn.record` is the first: "owner with reason, TL with reason, Admin with
reason, Super admin [with reason]"). ALWAYS_REQUIRED_KEYS overrides the Super-admin-optional rule
above for exactly those keys; every other key keeps the table as-is.
"""

from enum import StrEnum

from app.core.errors import ReasonRequired
from app.platform.permissions.roles import ActionClass, Role

ALWAYS_REQUIRED_KEYS: frozenset[str] = frozenset({"renewal_cancellation_churn.record"})


class ReasonRule(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    NONE = "none"


def reason_rule(role: Role, action_class: ActionClass, key: str | None = None) -> ReasonRule:
    if action_class == ActionClass.VIEW:
        return ReasonRule.NONE
    if role == Role.SUPER_ADMIN:
        if key in ALWAYS_REQUIRED_KEYS:
            return ReasonRule.REQUIRED
        return ReasonRule.OPTIONAL
    if role == Role.ADMIN:
        return ReasonRule.REQUIRED
    return ReasonRule.REQUIRED if action_class == ActionClass.IMPORTANT else ReasonRule.NONE


def normalise_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    reason = reason.strip()
    return reason or None


def check(
    role: Role, action_class: ActionClass, reason: str | None, key: str | None = None
) -> str | None:
    """Return the cleaned reason to store, or raise ReasonRequired."""
    cleaned = normalise_reason(reason)
    if reason_rule(role, action_class, key) == ReasonRule.REQUIRED and cleaned is None:
        raise ReasonRequired("A reason is required for this action")
    return cleaned
