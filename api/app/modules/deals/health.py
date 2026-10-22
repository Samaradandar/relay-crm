# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Deal health and flags (PRD 11 Feature 6). All day counts use IST calendar days.

- Nudge due : the next step has been in 'Waiting on them' for 2 or more days.
- Stale     : 7 or more days with no logged activity.
- At risk   : the next step is overdue.
- On track  : otherwise.
- Slipped   : the expected close date has passed while the deal is open (a flag, not a health).
Stale outranks At risk when both are true. There are no stage time limits (no SLA).
"""

from datetime import date, datetime
from typing import Any

from app.core.time import to_ist, utc_now
from app.modules.leads.models import Deal, NextStep

STALE_DAYS = 7
NUDGE_DAYS = 2


def ist_date(moment: datetime) -> date:
    return to_ist(moment).date()


def compute(
    deal: Deal, step: NextStep | None, now: datetime | None = None
) -> tuple[str, dict[str, bool]]:
    """(health, {nudge_due, slipped}) for an open deal. Other deals are simply on track."""
    now = now or utc_now()
    if deal.status != "open":
        return "on_track", {"nudge_due": False, "slipped": False}
    today = ist_date(now)
    ref = deal.last_activity_at or deal.assigned_at or deal.created_at
    stale = (today - ist_date(ref)).days >= STALE_DAYS
    overdue = bool(step and step.due_date < today)
    nudge = bool(
        step
        and step.lane == "waiting_on_them"
        and (today - ist_date(step.lane_since)).days >= NUDGE_DAYS
    )
    slipped = bool(deal.expected_close_date and deal.expected_close_date < today)
    health = "stale" if stale else "at_risk" if overdue else "on_track"
    return health, {"nudge_due": nudge, "slipped": slipped}


def apply(deal: Deal, step: NextStep | None, now: datetime | None = None) -> bool:
    """Write health and flags onto the deal. Returns True when anything changed."""
    health, fl = compute(deal, step, now)
    flags: dict[str, Any] = dict(deal.flags or {})
    changed = health != deal.health
    for k, v in fl.items():
        if bool(flags.get(k)) != v:
            changed = True
            if v:
                flags[k] = True
            else:
                flags.pop(k, None)
    if changed:
        deal.health, deal.flags = health, flags
    return changed
