# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox event names for activities."""

ACTIVITY_LOGGED = "activity.logged"
# Phase 8 (side track): the only other write path for an activity besides log_activity above.
ACTIVITY_EDITED = "activity.edited"
ACTIVITY_DELETED = "activity.deleted"
