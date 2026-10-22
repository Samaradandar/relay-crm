# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
import pytest

from app.core.errors import PermissionDenied, ReasonRequired
from app.platform.permissions.guard import Effect, evaluate, require
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import (
    ROLE_ORDER,
    ROUTE_ADMIN_POOL,
    ROUTE_TEAM_LEADER,
    matrix_keys,
    seed_table,
)
from app.platform.permissions.roles import ActionClass, Mode, Scope
from app.platform.reasons.policy import ReasonRule, check, reason_rule

TABLE = seed_table()
U, TL, AD, SA = ROLE_ORDER


def test_every_key_has_a_class_and_a_row_for_every_role():
    assert matrix_keys() == set(KEYS), "registry and matrix must list the same keys"
    for key, pk in KEYS.items():
        assert isinstance(pk.action_class, ActionClass), key
        for role in ROLE_ORDER:
            assert (role, key) in TABLE, f"missing matrix row {role} / {key}"
    assert len(TABLE) == len(KEYS) * 4


def test_never_allowed_rows_are_denied_for_everyone():
    for key in (
        "record.erase",
        "record.created_fields.edit",
        "activity.edit_others",
        "deal.bulk_stage_move",
    ):
        for role in ROLE_ORDER:
            assert evaluate(TABLE, role, key).effect == Effect.DENY


def test_super_admin_never_denied_except_system_locked_and_transfer_request():
    locked = {
        "record.erase",
        "record.created_fields.edit",
        "activity.edit_others",
        "deal.bulk_stage_move",
        "deal.team_transfer.request",
        "approval.request",
        # 6 Oct 2026 incentive decision: only a Team Leader *requests* a rate; the Super admin
        # sets it directly (incentive_rate.set), so "request" is denied to the SA by design
        "incentive.request",
    }
    for key in KEYS:
        if key in locked or key in ("criterion.tick", "criterion.untick", "deal.move_forward"):
            continue
        assert not TABLE[(SA, key)].denied, key


@pytest.mark.parametrize(
    ("role", "key", "scope", "mode"),
    [
        (U, "deal.view", Scope.OWN, Mode.ALLOW),
        (TL, "deal.view", Scope.TEAM, Mode.ALLOW),
        (AD, "deal.view", Scope.ALL, Mode.ALLOW),
        (U, "pool.view", Scope.NONE, Mode.ALLOW),
        (TL, "pool.view", Scope.TEAM, Mode.ALLOW),
        (TL, "sa_owned.view", Scope.NONE, Mode.ALLOW),
        (AD, "sa_owned.view", Scope.ALL, Mode.ALLOW),
        (U, "leaderboard.view", Scope.ALL, Mode.ALLOW),
        (U, "deal.value.above_10", Scope.OWN, Mode.NEEDS_APPROVAL),
        (TL, "deal.value.above_10", Scope.TEAM, Mode.NEEDS_APPROVAL),
        (TL, "deal.team_transfer.request", Scope.TEAM, Mode.REQUEST_ONLY),
        (AD, "deal.team_transfer.request", Scope.NONE, Mode.ALLOW),
        (TL, "deal.team_transfer.decide_or_move", Scope.NONE, Mode.ALLOW),
        (SA, "deal.team_transfer.decide_or_move", Scope.ALL, Mode.ALLOW),
        (TL, "user.manage", Scope.TEAM, Mode.REQUEST_ONLY),
        (AD, "admin.manage", Scope.NONE, Mode.ALLOW),
        (SA, "admin.manage", Scope.ALL, Mode.ALLOW),
        (TL, "invoice.edit", Scope.TEAM, Mode.NEEDS_APPROVAL),
        (U, "discount.up_to_10", Scope.OWN, Mode.NEEDS_APPROVAL),
        (TL, "discount.up_to_10", Scope.TEAM, Mode.ALLOW),
        (TL, "discount.above_10_or_below_floor", Scope.TEAM, Mode.NEEDS_APPROVAL),
        (U, "activity.delete", Scope.NONE, Mode.ALLOW),
        (TL, "approval.decide", Scope.TEAM, Mode.ALLOW),
        (U, "approval.decide", Scope.NONE, Mode.ALLOW),
        (U, "approval.request", Scope.OWN, Mode.ALLOW),
        (U, "password.reset_own", Scope.OWN, Mode.ALLOW),
        (TL, "password.reset_others", Scope.NONE, Mode.ALLOW),
        (U, "target.set", Scope.NONE, Mode.ALLOW),
        (TL, "teams.compare", Scope.NONE, Mode.ALLOW),
    ],
)
def test_prd_rows(role, key, scope, mode):
    rule = TABLE[(role, key)]
    assert rule.scope == scope
    assert rule.mode == mode


def test_approval_routing():
    assert TABLE[(U, "deal.value.above_10")].routed_to == ROUTE_TEAM_LEADER
    assert TABLE[(U, "discount.up_to_10")].routed_to == ROUTE_TEAM_LEADER
    assert TABLE[(U, "discount.above_10_or_below_floor")].routed_to == ROUTE_ADMIN_POOL
    assert TABLE[(TL, "deal.value.above_10")].routed_to == ROUTE_ADMIN_POOL
    assert TABLE[(TL, "user.manage")].routed_to == ROUTE_ADMIN_POOL


def test_own_deals_only_rules_fail_closed():
    for role in (TL, AD, SA):
        assert evaluate(TABLE, role, "criterion.tick", is_own_record=True).allowed
        assert evaluate(TABLE, role, "criterion.tick", is_own_record=False).effect == Effect.DENY
        assert evaluate(TABLE, role, "criterion.tick").effect == Effect.DENY  # unknown: deny
    assert evaluate(TABLE, U, "criterion.tick").allowed  # users: own scope handled by scope.py


def test_tl_cannot_override_own_deals_but_can_on_team_deals():
    assert evaluate(TABLE, TL, "deal.override_or_skip", is_own_record=False).allowed
    assert evaluate(TABLE, TL, "deal.override_or_skip", is_own_record=True).effect == Effect.DENY
    assert evaluate(TABLE, TL, "deal.override_or_skip").effect == Effect.DENY
    assert evaluate(TABLE, AD, "deal.override_or_skip").allowed


def test_require_raises_403_and_unknown_key_is_a_bug():
    with pytest.raises(PermissionDenied) as exc:
        require(TABLE, U, "pool.assign")
    assert exc.value.status_code == 403 and exc.value.details == {"key": "pool.assign"}
    assert require(TABLE, TL, "pool.assign").allowed
    assert require(TABLE, U, "deal.value.above_10").effect == Effect.NEEDS_APPROVAL
    with pytest.raises(ValueError):
        evaluate(TABLE, U, "no.such.key")


# --- reason policy (PRD 4.2) ---------------------------------------------------------------
IMP, RTN, VIEW = ActionClass.IMPORTANT, ActionClass.ROUTINE, ActionClass.VIEW
REQ, OPT, NONE = ReasonRule.REQUIRED, ReasonRule.OPTIONAL, ReasonRule.NONE


@pytest.mark.parametrize(
    ("role", "klass", "expected"),
    [
        (U, IMP, REQ),
        (TL, IMP, REQ),
        (AD, IMP, REQ),
        (SA, IMP, OPT),
        (U, RTN, NONE),
        (TL, RTN, NONE),
        (AD, RTN, REQ),
        (SA, RTN, OPT),
        (U, VIEW, NONE),
        (TL, VIEW, NONE),
        (AD, VIEW, NONE),
        (SA, VIEW, NONE),
    ],
)
def test_reason_policy_table(role, klass, expected):
    assert reason_rule(role, klass) == expected


@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t "])
def test_missing_or_whitespace_reason_rejected_where_required(bad):
    for role, klass in [(U, IMP), (TL, IMP), (AD, IMP), (AD, RTN)]:
        with pytest.raises(ReasonRequired) as exc:
            check(role, klass, bad)
        assert exc.value.status_code == 422
    assert check(SA, IMP, bad) is None
    assert check(U, RTN, bad) is None
    assert check(U, VIEW, bad) is None


def test_reason_is_trimmed_and_any_length_accepted():
    assert check(U, IMP, "  x  ") == "x"
    assert check(AD, RTN, "ok") == "ok"
    assert check(SA, IMP, " because ") == "because"
