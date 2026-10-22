# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox event names this module emits and listens to.

Listens to: deal.won (drafts the subscription).
"""

SUBSCRIPTION_DRAFTED = "subscription.drafted"
SUBSCRIPTION_CONFIRMED = "subscription.confirmed"
SUBSCRIPTION_CHANGED = "subscription.changed"
SUBSCRIPTION_CHANGE_RAISED = "subscription_change.raised"
SUBSCRIPTION_CHANGE_CONFIRMED = "subscription_change.confirmed"
SUBSCRIPTION_CHANGE_REJECTED = "subscription_change.rejected"
# Phase 5 (P5-03): renewal / cancellation recording. SUBSCRIPTION_CANCELLATION_REQUESTED is the
# trigger point for a later PR's churn-risk alert to Admins/Super admins -- this module only
# emits it, it never builds the alert itself.
SUBSCRIPTION_RENEWAL_RECORDED = "subscription.renewal_recorded"
SUBSCRIPTION_CANCELLATION_REQUESTED = "subscription.cancellation_requested"
SUBSCRIPTION_CANCELLED = "subscription.cancelled"
SUBSCRIPTION_CANCELLATION_REVERTED = "subscription.cancellation_reverted"
