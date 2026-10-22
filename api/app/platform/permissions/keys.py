# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Registry of every permission key and its class (important, routine or view).

A key without a class, or without a matrix row for every role, fails CI (see tests).
Two PRD rows mix classes, so each is split into two keys:
  criterion.verify (routine) / criterion.reject (important)
  password.reset_own (routine) / password.reset_others (important)
"""

from dataclasses import dataclass

from app.platform.permissions.roles import ActionClass

I, R, V = ActionClass.IMPORTANT, ActionClass.ROUTINE, ActionClass.VIEW  # noqa: E741


@dataclass(frozen=True)
class PermissionKey:
    key: str
    action_class: ActionClass
    description: str


_KEYS: list[tuple[str, ActionClass, str]] = [
    # A. Visibility
    ("deal.view", V, "View deals, accounts, contacts, activities and notes"),
    ("board.view", V, "Kanban board and stage pages, incl. Won and Lost"),
    ("pool.view", V, "Lead Pool"),
    ("today.view", V, "Today view"),
    ("customer.view", V, "Customers, subscriptions, revenue, forecast, Churn History"),
    ("audit.view", V, "Change history and audit log"),
    ("deleted.view", V, "Deleted records"),
    ("sa_owned.view", V, "Deals owned by a Super admin"),
    # B. Leads, Pool, deals
    ("lead.create", R, "Create a lead (enters the Lead Pool)"),
    ("pool.assign", I, "Assign a lead from the Lead Pool manually"),
    ("lead.reassign", I, "Reassign a lead"),
    ("deal.edit", R, "Edit deal fields"),
    ("deal.value.up_to_5", R, "Change deal value by up to 5%"),
    ("deal.value.5_to_10", I, "Change deal value by more than 5%, up to 10%"),
    ("deal.value.above_10", I, "Change deal value by more than 10%"),
    ("deal.source.change_after_lead", I, "Change lead source after the Lead stage"),
    ("deal.close_date.change", I, "Change expected close date after it is set"),
    ("deal.pricing.change_after_prospecting", I, "Change pricing model, plan or volume"),
    ("deal.delete", I, "Soft-delete a deal"),
    ("deal.restore", I, "Restore a deleted deal"),
    ("record.created_fields.edit", I, "Edit created date or created by (system-locked)"),
    ("record.erase", I, "Permanently erase a record (never allowed)"),
    # C. Activities
    ("activity.log", R, "Log an activity"),
    ("activity.edit_own", I, "Edit own activity (24 h window for User and TL)"),
    ("activity.edit_others", I, "Edit another person's activity (never allowed)"),
    ("activity.delete", I, "Delete an activity"),
    # D. Pipeline movement
    ("criterion.tick", R, "Tick an exit criterion (proof required)"),
    ("criterion.untick", I, "Untick an exit criterion"),
    ("deal.move_forward", R, "Move forward (all criteria ticked)"),
    ("criterion.verify", R, "Verify a tick"),
    ("criterion.reject", I, "Reject a tick during verification"),
    ("deal.move_backward", I, "Move backward"),
    ("deal.lose", I, "Close as Lost"),
    ("deal.reopen", I, "Reopen a Lost deal"),
    ("deal.override_or_skip", I, "Skip a stage or override exit criteria"),
    ("deal.bulk_lose", I, "Bulk close as Lost"),
    ("deal.bulk_stage_move", I, "Bulk stage move (never allowed)"),
    # E. Next steps and Today
    ("next_step.set", R, "Set next step and due date"),
    ("deal.snooze_nudge_replied", R, "Snooze, Nudge, They replied"),
    ("deal.bulk_snooze", I, "Bulk snooze"),
    # F. Ownership
    ("deal.owner.change", I, "Change a deal's owner"),
    ("deal.team_transfer.request", I, "Request a cross-team transfer (give or take)"),
    ("deal.team_transfer.decide_or_move", I, "Decide a transfer or move a deal to another team"),
    ("deal.bulk_reassign", I, "Bulk reassign a departing rep's deals"),
    # G. Proposals and discounts
    ("proposal.send_list_price", R, "Generate a proposal and send at list price"),
    ("discount.up_to_10", I, "Any discount up to 10%"),
    ("discount.above_10_or_below_floor", I, "Discount above 10%, or below floor price"),
    ("catalog.template.manage", I, "Edit proposal template, T&C, price list, catalog, floor price"),
    # H. Agreements, invoices, payments
    ("agreement.add", R, "Add an agreement; set status Sent or Countersigned"),
    ("agreement.edit", I, "Edit an existing agreement"),
    ("contract_dates.set_and_subscription.confirm", I, "Contract dates; confirm subscriptions"),
    ("payment.record", I, "Record payment status and upload payment proof"),
    ("invoice.add", R, "Add an invoice"),
    ("invoice.edit", I, "Edit an existing invoice"),
    ("agreement.terminate", I, "Mark an agreement Terminated"),
    ("renewal_cancellation_churn.record", I, "Record renewal, cancellation or churn"),
    ("churn.reverse", I, "Reverse a churn recorded by mistake (Admin / Super admin only)"),
    ("churn_reasons.manage", I, "Edit the org's churn reasons list (Admin / Super admin only)"),
    # I. Reports, profiles, export
    ("profile.view_own", V, "View own profile, win rate and metrics"),
    ("profile.view_others", V, "View other people's profiles and the monitoring view"),
    ("leaderboard.view", V, "View the Leaderboard"),
    ("target.set", I, "Set or change a rep's monthly target"),
    ("incentive_rate.set", I, "Set the incentive % on a deal directly (Admin/SA only)"),
    ("incentive.view", V, "See incentives (per deal and total received)"),
    (
        "incentive.request",
        I,
        "Request an incentive rate on a Won deal, or pledge one on an Active deal",
    ),
    ("teams.compare", V, "Compare teams"),
    ("reports.view", V, "View reports and analytics (Phase 6)"),
    ("forecast.view", V, "View the weighted forecast (Phase 6)"),
    ("performance.recompute", I, "Recompute a closed leaderboard month (Admin / Super admin)"),
    ("capture.settings.manage", I, "Manage website forms and the email capture source (Phase 7)"),
    ("capture.csv.upload", I, "Upload a CSV of leads (Phase 7)"),
    ("capture.review.view", V, "View the capture review queue (Phase 7)"),
    ("capture.review.decide", I, "Decide a held capture: create, merge or discard (Phase 7)"),
    ("capture.events.view", V, "Monitor capture sources and outcomes (Phase 7)"),
    ("google.connect", V, "Connect, disconnect or reconnect your own Google account (Phase 8)"),
    ("google.admin.view", V, "View everyone's Google sync health (Admin / Super admin, Phase 8)"),
    ("google.admin.retry", I, "Retry or nudge another rep's Google sync (Admin / Super admin)"),
    ("gmail.log", I, "Search and log your own Gmail threads on a deal (Phase 8)"),
    ("export.create", I, "Export to CSV (every export logged)"),
    ("export_log.view", V, "View export log"),
    # J. Approvals
    ("approval.request", I, "Request approval for a blocked action"),
    ("approval.decide", I, "Approve or reject requests"),
    # K. Settings and rules
    ("pipeline.config", I, "Edit stages, transitions, criteria, proof rules, probabilities"),
    ("assignment.config", I, "Set assignment rules, default team and each rep's lead cap"),
    ("custom_fields_and_reason_lists.manage", I, "Custom fields, activity types, reason lists"),
    ("duplicate.override_or_merge", I, "Override a duplicate block; merge duplicate accounts"),
    ("duplicate.create_anyway", I, "Choose Create anyway on a duplicate warning"),
    # L. Users and teams
    ("user.manage", I, "Create, deactivate or change the role of Users and Team Leaders"),
    ("team.manage", I, "Create teams; move users between teams"),
    ("team.colour.change", I, "Change a team's colour"),
    ("admin.manage", I, "Create, promote or remove Admins and Super admins"),
    ("password.reset_own", R, "Reset own password"),
    ("password.reset_others", I, "Reset someone else's password"),
    # M. Operations
    ("jobs.view_failed", V, "List failed background jobs"),
    ("jobs.retry", I, "Retry a failed background job"),
]

KEYS: dict[str, PermissionKey] = {k: PermissionKey(k, c, d) for k, c, d in _KEYS}

if len(KEYS) != len(_KEYS):
    raise RuntimeError("duplicate permission key in registry")
