# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox event names this module emits and listens to."""

DEAL_POOLED = "deal.pooled"  # a lead entered the Pool: the assignment engine picks it up
LEAD_ASSIGNED = "lead.assigned"  # first owner (rule, round-robin or manual)
LEAD_WAITING = "lead.waiting"  # nobody eligible: the routed Team Leader is told once
REP_LEAD_WAITING = "lead.rep_waiting"  # a rep's new lead waits for the Team Leader's decision
DEAL_OWNER_CHANGED = "deal.owner_changed"
DEAL_UPDATED = "deal.updated"
POOL_CHANGED = "pool.changed"
ASSIGNMENT_RETRY = "assignment.retry"  # something changed that may free a waiting lead
DUPLICATE_OUTSIDE_SCOPE = "duplicate.created_anyway_outside_scope"
TRANSFER_REQUESTED = "transfer.requested"
TRANSFER_DECIDED = "transfer.decided"
TRANSFER_COMMENTED = "transfer.commented"

# Events from other modules that can free a waiting lead (a rep appears, returns or moves team).
RETRY_TRIGGERS = (
    "user.created",
    "user.reactivated",
    "user.team_changed",
    ASSIGNMENT_RETRY,
    "deal.closed",
)
