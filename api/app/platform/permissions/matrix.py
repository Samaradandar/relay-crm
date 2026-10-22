# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Role x key x scope, seeded from PRD sections A-L (PRD 4.4).

The matrix is data. The same rows are inserted into `role_permissions` by the migration seed
and used directly by tests. Cell syntax, in role order (user, team_leader, admin, super_admin):

  "-"            denied
  "own|team|all" allowed within that scope
  "own_deals"    allowed, but only on deals the caller personally owns
  "team_not_own" allowed within the team, but not on the caller's own deals
  "appr_tl"      needs approval, routed to the requester's Team Leader
  "appr_admin"   needs approval, routed to the Admin / Super admin pool
  "req_admin"    request only (the actor never performs it), routed to Admin / Super admin pool

The Admin "reason on every change" and Super admin "reason optional" rules are applied by
`reasons.policy`, not stored here.
"""

from dataclasses import dataclass

from app.platform.permissions.keys import KEYS
from app.platform.permissions.roles import Mode, Role, Scope

ROLE_ORDER = (Role.USER, Role.TEAM_LEADER, Role.ADMIN, Role.SUPER_ADMIN)

ROUTE_TEAM_LEADER = "team_leader"
ROUTE_ADMIN_POOL = "admin_pool"


@dataclass(frozen=True)
class Rule:
    scope: Scope
    mode: Mode
    own_deals_only: bool = False
    not_own_deals: bool = False
    routed_to: str | None = None

    @property
    def denied(self) -> bool:
        return self.scope == Scope.NONE


DENIED = Rule(Scope.NONE, Mode.ALLOW)

_MATRIX: dict[str, tuple[str, str, str, str]] = {
    # A. Visibility
    "deal.view": ("own", "team", "all", "all"),
    "board.view": ("own", "team", "all", "all"),
    "pool.view": ("-", "team", "all", "all"),
    "today.view": ("own", "team", "all", "all"),
    "customer.view": ("own", "team", "all", "all"),
    "audit.view": ("own", "team", "all", "all"),
    "deleted.view": ("-", "team", "all", "all"),
    "sa_owned.view": ("-", "-", "all", "all"),
    # B. Leads, Pool, deals
    "lead.create": ("own", "team", "all", "all"),
    "pool.assign": ("-", "team", "all", "all"),
    "lead.reassign": ("-", "team", "all", "all"),
    "deal.edit": ("own", "team", "all", "all"),
    "deal.value.up_to_5": ("own", "team", "all", "all"),
    "deal.value.5_to_10": ("own", "team", "all", "all"),
    "deal.value.above_10": ("appr_tl", "appr_admin", "all", "all"),
    "deal.source.change_after_lead": ("-", "team", "all", "all"),
    "deal.close_date.change": ("own", "team", "all", "all"),
    "deal.pricing.change_after_prospecting": ("own", "team", "all", "all"),
    "deal.delete": ("-", "team", "all", "all"),
    "deal.restore": ("-", "team", "all", "all"),
    "record.created_fields.edit": ("-", "-", "-", "-"),
    "record.erase": ("-", "-", "-", "-"),
    # C. Activities
    "activity.log": ("own", "team", "all", "all"),
    "activity.edit_own": ("own", "own", "all", "all"),
    "activity.edit_others": ("-", "-", "-", "-"),
    "activity.delete": ("-", "team", "all", "all"),
    # D. Pipeline movement
    "criterion.tick": ("own", "own_deals", "own_deals", "own_deals"),
    "criterion.untick": ("own", "own_deals", "own_deals", "own_deals"),
    "deal.move_forward": ("own", "own_deals", "own_deals", "own_deals"),
    "criterion.verify": ("-", "team", "all", "all"),
    "criterion.reject": ("-", "team", "all", "all"),
    "deal.move_backward": ("own", "team", "all", "all"),
    "deal.lose": ("own", "team", "all", "all"),
    "deal.reopen": ("-", "team", "all", "all"),
    "deal.override_or_skip": ("-", "team_not_own", "all", "all"),
    "deal.bulk_lose": ("-", "team", "all", "all"),
    "deal.bulk_stage_move": ("-", "-", "-", "-"),
    # E. Next steps and Today
    "next_step.set": ("own", "team", "all", "all"),
    "deal.snooze_nudge_replied": ("own", "team", "all", "all"),
    "deal.bulk_snooze": ("-", "team", "all", "all"),
    # F. Ownership
    "deal.owner.change": ("-", "team", "all", "all"),
    "deal.team_transfer.request": ("-", "req_admin", "-", "-"),
    "deal.team_transfer.decide_or_move": ("-", "-", "all", "all"),
    "deal.bulk_reassign": ("-", "team", "all", "all"),
    # G. Proposals and discounts
    "proposal.send_list_price": ("own", "team", "all", "all"),
    "discount.up_to_10": ("appr_tl", "team", "all", "all"),
    "discount.above_10_or_below_floor": ("appr_admin", "appr_admin", "all", "all"),
    "catalog.template.manage": ("-", "-", "all", "all"),
    # H. Agreements, invoices, payments
    "agreement.add": ("own", "team", "all", "all"),
    "agreement.edit": ("-", "team", "all", "all"),
    "contract_dates.set_and_subscription.confirm": ("-", "team", "all", "all"),
    "payment.record": ("-", "team", "all", "all"),
    "invoice.add": ("own", "team", "all", "all"),
    "invoice.edit": ("-", "appr_admin", "all", "all"),
    "agreement.terminate": ("-", "-", "all", "all"),
    "renewal_cancellation_churn.record": ("own", "team", "all", "all"),
    # stricter than the key above: reversing a churn recorded by mistake is Admin/SA only (not
    # TL/owner), same own/team-denied shape as catalog.template.manage.
    "churn.reverse": ("-", "-", "all", "all"),
    # Settings list (PRD Feature 9a): same Admin+SA-only shape as churn.reverse above.
    "churn_reasons.manage": ("-", "-", "all", "all"),
    # I. Reports, profiles, export
    "profile.view_own": ("own", "own", "all", "all"),
    "profile.view_others": ("-", "team", "all", "all"),
    "leaderboard.view": ("all", "all", "all", "all"),
    "target.set": ("-", "team", "all", "all"),
    # Leaderboard & Incentive module (approved 6 Oct 2026): a TL can no longer set directly,
    # only request/pledge (see incentive.request below) -- Admin/SA set or approve.
    "incentive_rate.set": ("-", "-", "all", "all"),
    "incentive.view": ("own", "team", "all", "all"),
    "incentive.request": ("-", "req_admin", "-", "-"),
    "teams.compare": ("-", "-", "all", "all"),
    # Phase 6: same own/team/all/all shape as deal.view -- a report is scoped the same as the
    # records it reports on.
    "reports.view": ("own", "team", "all", "all"),
    "forecast.view": ("own", "team", "all", "all"),
    # Admin+SA-only, same shape as churn.reverse: fixing a closed month's numbers is a correction,
    # not routine reporting.
    "performance.recompute": ("-", "-", "all", "all"),
    # Phase 7: website forms / email source config, Admin+SA only (same shape as pipeline.config).
    "capture.settings.manage": ("-", "-", "all", "all"),
    # A TL can upload for their own team; Admin/SA for any team.
    "capture.csv.upload": ("-", "team", "all", "all"),
    "capture.review.view": ("-", "team", "all", "all"),
    "capture.review.decide": ("-", "team", "all", "all"),
    "capture.events.view": ("-", "-", "all", "all"),
    # Phase 8: a rep manages only their own Google connection; Admin/SA see health, never tokens.
    "google.connect": ("own", "own", "own", "own"),
    "google.admin.view": ("-", "-", "all", "all"),
    "google.admin.retry": ("-", "-", "all", "all"),
    "gmail.log": ("own", "own", "own", "own"),
    "export.create": ("own", "team", "all", "all"),
    "export_log.view": ("-", "team", "all", "all"),
    # J. Approvals ("Not needed" for Admin and Super admin: they act directly)
    "approval.request": ("own", "own", "-", "-"),
    "approval.decide": ("-", "team", "all", "all"),
    # K. Settings and rules
    "pipeline.config": ("-", "-", "all", "all"),
    "assignment.config": ("-", "-", "all", "all"),
    "custom_fields_and_reason_lists.manage": ("-", "-", "all", "all"),
    "duplicate.override_or_merge": ("-", "-", "all", "all"),
    "duplicate.create_anyway": ("own", "team", "all", "all"),
    # L. Users and teams
    "user.manage": ("-", "req_admin", "all", "all"),
    "team.manage": ("-", "-", "all", "all"),
    "team.colour.change": ("-", "-", "all", "all"),
    "admin.manage": ("-", "-", "-", "all"),
    # Self-service reset is open to every role (Build plan P1-01); resetting others is not.
    "password.reset_own": ("own", "own", "own", "own"),
    "password.reset_others": ("-", "-", "all", "all"),
    # M. Operations
    "jobs.view_failed": ("-", "-", "all", "all"),
    "jobs.retry": ("-", "-", "all", "all"),
}


def _parse(cell: str, role: Role) -> Rule:
    if cell == "-":
        return DENIED
    if cell in ("own", "team", "all"):
        return Rule(Scope(cell), Mode.ALLOW)
    if cell == "own_deals":
        return Rule(Scope.OWN, Mode.ALLOW, own_deals_only=True)
    if cell == "team_not_own":
        return Rule(Scope.TEAM, Mode.ALLOW, not_own_deals=True)
    scope = Scope.OWN if role == Role.USER else Scope.TEAM
    if cell == "appr_tl":
        return Rule(scope, Mode.NEEDS_APPROVAL, routed_to=ROUTE_TEAM_LEADER)
    if cell == "appr_admin":
        return Rule(scope, Mode.NEEDS_APPROVAL, routed_to=ROUTE_ADMIN_POOL)
    if cell == "req_admin":
        return Rule(scope, Mode.REQUEST_ONLY, routed_to=ROUTE_ADMIN_POOL)
    raise ValueError(f"unknown matrix cell {cell!r}")


def seed_rows() -> list[tuple[Role, str, Rule]]:
    """Every (role, key, rule) row, in a stable order, for the migration seed and tests."""
    rows: list[tuple[Role, str, Rule]] = []
    for key, cells in _MATRIX.items():
        for role, cell in zip(ROLE_ORDER, cells, strict=True):
            rows.append((role, key, _parse(cell, role)))
    return rows


def seed_table() -> dict[tuple[Role, str], Rule]:
    return {(role, key): rule for role, key, rule in seed_rows()}


def matrix_keys() -> set[str]:
    return set(_MATRIX)


def registry_matches_matrix() -> bool:
    return matrix_keys() == set(KEYS)
