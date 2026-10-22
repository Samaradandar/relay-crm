# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox event names for the stage engine."""

DEAL_MOVED = "deal.moved"
DEAL_WON = "deal.won"
DEAL_LOST = "deal.lost"
DEAL_REOPENED = "deal.reopened"
DEAL_OVERRIDDEN = "deal.overridden"
# A deal stopped being open (won or lost): the rep's lead cap frees up, so waiting leads retry.
DEAL_CLOSED = "deal.closed"
CRITERION_REJECTED = "criterion.rejected"
DEAL_REVIEW_NEEDED = "deal.review_needed"
DEAL_PROOF_MISSING = "deal.proof_missing"
# Every Onboarding exit criterion is ticked: the Team Leader is asked to set the incentive.
ONBOARDING_COMPLETED = "deal.onboarding_completed"
