# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Permission decision for (role, key). Every write calls this before anything else.

`evaluate` is the pure decision over a permission table (loaded from `role_permissions`, or the
seed in tests). The scope check on the actual record is `scope.py`. Services combine both.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import PermissionDenied
from app.platform.permissions.matrix import Rule
from app.platform.permissions.roles import Mode, Role, Scope

PermissionTable = Mapping[tuple[Role, str], Rule]


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"
    REQUEST_ONLY = "request_only"


@dataclass(frozen=True)
class Decision:
    effect: Effect
    scope: Scope
    rule: Rule | None = None

    @property
    def allowed(self) -> bool:
        return self.effect == Effect.ALLOW


_DENY = Decision(Effect.DENY, Scope.NONE)


def evaluate(
    table: PermissionTable, role: Role, key: str, *, is_own_record: bool | None = None
) -> Decision:
    """Decide what `role` may do for `key`.

    `is_own_record` is whether the target is owned by the caller; it only matters for rules
    flagged own_deals_only / not_own_deals. If such a rule is hit and ownership is unknown the
    answer is deny (fail closed).
    """
    rule = table.get((role, key))
    if rule is None:
        raise ValueError(f"unknown permission key {key!r} for role {role}")
    if rule.denied:
        return _DENY
    if rule.own_deals_only and is_own_record is not True:
        return _DENY
    if rule.not_own_deals and is_own_record is not False:
        return _DENY
    effect = {
        Mode.ALLOW: Effect.ALLOW,
        Mode.NEEDS_APPROVAL: Effect.NEEDS_APPROVAL,
        Mode.REQUEST_ONLY: Effect.REQUEST_ONLY,
    }[rule.mode]
    return Decision(effect, rule.scope, rule)


def require(
    table: PermissionTable, role: Role, key: str, *, is_own_record: bool | None = None
) -> Decision:
    """Like evaluate, but raises 403 permission_denied on deny. Approval modes are returned
    so the service can turn them into an approval request (202)."""
    decision = evaluate(table, role, key, is_own_record=is_own_record)
    if decision.effect == Effect.DENY:
        raise PermissionDenied("You are not allowed to do this", {"key": key})
    return decision
