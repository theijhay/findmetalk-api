"""Validate live sessions and enforce the suspended account allowlist (FR-AUTH-3)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.v1.models.auth.auth import User
from api.v1.services.auth.sessions import access_identity, active_user

_bearer = HTTPBearer(auto_error=False)


def bearer_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tuple[UUID, UUID]:
    if credentials is None:
        raise HTTPException(401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return access_identity(credentials.credentials)


async def get_signed_in_user(identity: Annotated[tuple[UUID, UUID], Depends(bearer_identity)]) -> User:
    """Allow suspended users only on routes explicitly permitted by the appeals contract."""
    return await active_user(*identity)


async def get_current_user(user: Annotated[User, Depends(get_signed_in_user)]) -> User:
    if user.status == "suspended":
        raise HTTPException(403, detail="account.suspended")
    return user
