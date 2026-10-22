# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox event names this module emits and listens to.

Listens to Phase 3 deal/stage events (pipeline.engine.events), Phase 2 ownership events
(leads.events) and Phase 1 team/user events (users.events) to keep rep_month_stats live; see
handlers.py. Incentives cannot be kept live the same way: Phase 4's payments.py emits no outbox
event today, so incentive amounts are recalculated lazily on read and by the nightly job
(stats.py's `recompute_incentives_for_org`), not by a payment-recorded listener. That is a
deliberate scope limit, not an oversight -- flagged in the Phase 6 build notes.
"""

TARGET_SET = "target.set"
INCENTIVE_RATE_SET = "incentive_rate.set"
TEAM_COLOUR_CHANGED = "team.colour_changed"
STATS_RECOMPUTED = "performance.stats_recomputed"  # live-update signal for the leaderboard
