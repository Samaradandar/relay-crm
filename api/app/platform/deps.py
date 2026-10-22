# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""FastAPI dependencies: who is calling, and request metadata for the audit trail."""

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.core.errors import PermissionDenied, Unauthenticated
from app.core.security import AuthProvider, Identity, get_auth_provider
from app.modules.users.models import User
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.roles import Role


def request_info(request: Request) -> RequestInfo:
    return RequestInfo(
        request_id=request.headers.get("x-request-id") or uuid.uuid4().hex,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("Missing bearer token")
    return token


async def actor_for_identity(identity: Identity) -> Actor:
    """Match the identity to a CRM user and apply the login rules. Checked on EVERY request, so
    a deactivated user's token stops working immediately."""
    async with get_sessionmaker()() as s:
        user = (
            await s.execute(
                select(User).where(
                    func.lower(User.email) == identity.email.lower(), User.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    if user is None:
        raise Unauthenticated("No CRM account exists for this email. Ask an Admin to add you.")
    if not user.is_active:
        raise Unauthenticated("This account is deactivated")
    if user.role == Role.USER and user.team_id is None:
        raise PermissionDenied("You are not in a team yet. Ask an Admin to add you to one.")
    return Actor(
        id=user.id,
        org_id=user.org_id,
        role=user.role,
        team_id=user.team_id,
        name=user.name,
        email=user.email,
    )


async def get_actor(
    request: Request, provider: Annotated[AuthProvider, Depends(get_auth_provider)]
) -> Actor:
    identity = await provider.verify(_bearer(request))
    return await actor_for_identity(identity)


ActorDep = Annotated[Actor, Depends(get_actor)]
RequestDep = Annotated[RequestInfo, Depends(request_info)]
