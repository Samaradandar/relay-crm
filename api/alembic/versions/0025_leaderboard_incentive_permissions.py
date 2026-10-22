# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Leaderboard & Incentive module: a Team Leader can no longer set incentive_rate.set directly
(now Admin/SA only) and instead gets the new incentive.request key (request/pledge, routed to
the Admin pool). Same pattern as 0015/0020/0022.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa

from alembic import op
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_rows
from app.platform.permissions.roles import Role

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

NEW_KEYS = ("incentive.request",)


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
    # incentive_rate.set: a Team Leader is now denied direct-set (scope none, mode allow, no route)
    op.execute(
        sa.text(
            "UPDATE role_permissions SET scope = 'none', mode = 'allow', own_deals_only = false, "
            "not_own_deals = false, routed_to = NULL "
            "WHERE key = 'incentive_rate.set' AND role = :r"
        ).bindparams(r=Role.TEAM_LEADER.value)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE role_permissions SET scope = 'team', mode = 'allow', own_deals_only = false, "
            "not_own_deals = false, routed_to = NULL "
            "WHERE key = 'incentive_rate.set' AND role = :r"
        ).bindparams(r=Role.TEAM_LEADER.value)
    )
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
