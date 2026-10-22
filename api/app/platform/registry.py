# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""One place that wires modules into the platform: scoped entities, approval executors, outbox
handlers. Idempotent. Later phases add their entities and request types here."""

from app.modules.accounts.models import Account
from app.modules.billing import invoices as invoices_service
from app.modules.catalog import service as catalog_service
from app.modules.customers.models import Customer
from app.modules.documents import service as documents_service
from app.modules.leads import transfers
from app.modules.leads.models import Deal
from app.modules.leads.service import on_user_team_moved
from app.modules.performance import service as performance_service
from app.modules.performance.models import Incentive
from app.modules.users import service as users_service
from app.modules.users.models import Team, User
from app.platform.approvals.models import ApprovalRequest
from app.platform.files.service import register_file_owner
from app.platform.permissions.scope import ScopedEntity, register_entity

_done = False


def setup() -> None:
    global _done
    if _done:
        return
    register_entity("user", ScopedEntity(User, owner_attr="id", hide_sa_owned=False))
    register_entity(
        "approval", ScopedEntity(ApprovalRequest, owner_attr="requested_by", hide_sa_owned=False)
    )
    # Team history: the team's own leader and Admins (BUG-P1-17); a rep never matches.
    register_entity(
        "team", ScopedEntity(Team, owner_attr="leader_id", team_attr="id", hide_sa_owned=False)
    )
    register_entity("deal", ScopedEntity(Deal, owner_attr="owner_id", team_attr="team_id"))
    register_entity(
        "account", ScopedEntity(Account, owner_attr="account_owner_id", hide_sa_owned=False)
    )
    register_entity(
        "customer", ScopedEntity(Customer, owner_attr="account_owner_id", hide_sa_owned=False)
    )
    # Phase 6: targets are scoped like the "user" entity (owner_attr="id") already registered
    # above -- target.set's entity check reuses ("user", target_user_id), not a new entity.
    register_entity("incentive", ScopedEntity(Incentive, owner_attr="user_id", hide_sa_owned=False))
    users_service.register()
    transfers.register()
    catalog_service.register()
    performance_service.register()
    documents_service.register()
    invoices_service.register()
    if on_user_team_moved not in users_service.TEAM_MOVE_HOOKS:
        users_service.TEAM_MOVE_HOOKS.append(on_user_team_moved)
    register_file_owner("user", "user.manage", "audit.view")
    register_file_owner("deal", "deal.edit", "deal.view")
    import app.modules.billing.alerts_p5  # noqa: F401  (registers the cancellation-requested churn-risk handler)
    import app.modules.billing.changes  # noqa: F401  (registers the existing_customer deal.won handler)
    import app.modules.billing.draft  # noqa: F401  (registers the deal.won draft handler)
    import app.modules.customers.service  # noqa: F401  (registers the deal.won handler)
    import app.modules.deals.handlers  # noqa: F401
    import app.modules.exports.service  # noqa: F401
    import app.modules.google.handlers  # noqa: F401
    import app.modules.leads.handlers  # noqa: F401
    import app.modules.performance.handlers  # noqa: F401
    import app.platform.notifications.handlers  # noqa: F401  (registers outbox handlers)
    import app.platform.realtime.service  # noqa: F401

    _done = True
