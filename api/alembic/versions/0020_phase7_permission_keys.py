# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 7: seed the five new capture permission keys into permission_keys/role_permissions,
same pattern as 0015 did for Phase 6's new keys.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa

from alembic import op
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_rows

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

NEW_KEYS = (
    "capture.settings.manage", "capture.csv.upload", "capture.review.view",
    "capture.review.decide", "capture.events.view",
)  # fmt: skip


def upgrade() -> None:
    for name in NEW_KEYS:
        key = KEYS[name]
        op.execute(
            sa.text(
                "INSERT INTO permission_keys (key, action_class, description) "
                "VALUES (:k, :c, :d) ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key.key, c=key.action_class.value, d=key.description)
        )
    for role, key_name, rule in seed_rows():
        if key_name not in NEW_KEYS:
            continue
        op.execute(
            sa.text(
                "INSERT INTO role_permissions "
                "(role, key, scope, mode, own_deals_only, not_own_deals, routed_to) "
                "VALUES (:r, :k, :s, :m, :o, :n, :t) ON CONFLICT (role, key) DO NOTHING"
            ).bindparams(
                r=role.value,
                k=key_name,
                s=rule.scope.value,
                m=rule.mode.value,
                o=rule.own_deals_only,
                n=rule.not_own_deals,
                t=rule.routed_to,
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM role_permissions WHERE key = ANY(:keys)").bindparams(
            keys=list(NEW_KEYS)
        )
    )
    op.execute(
        sa.text("DELETE FROM permission_keys WHERE key = ANY(:keys)").bindparams(
            keys=list(NEW_KEYS)
        )
    )
