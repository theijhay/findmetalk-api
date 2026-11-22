"""Public email OTP endpoints (FR-AUTH-1, FR-AUTH-4)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from api.core.client_ip import get_client_ip
from api.core.errors import ErrorResponse
from api.v1.models.auth.auth import User
from api.v1.schemas.auth.auth import (
    CodeRequest,
    CodeResponse,
    RefreshRequest,
    TokenPair,
    UserMe,
    VerifyRequest,
    VerifyResponse,
)
from api.v1.services.auth.dependencies import bearer_identity, get_current_user, get_signed_in_user
from api.v1.services.auth.email import EmailSender, get_email_sender
from api.v1.services.auth.otp import request_code, verify_code
from api.v1.services.auth.sessions import revoke_sessions, rotate_refresh_token, user_me

auth = APIRouter(prefix="/auth", tags=["Auth"])
account = APIRouter(tags=["Auth"])


@auth.post(
    "/code",
    status_code=202,
    response_model=CodeResponse,
    responses={429: {"model": ErrorResponse, "description": "too_many_requests"}},
)
async def send_code(
    body: CodeRequest, request: Request, sender: Annotated[EmailSender, Depends(get_email_sender)]
) -> CodeResponse:
    return await request_code(body.email, get_client_ip(request), sender)


@auth.post(
    "/verify",
    response_model=VerifyResponse,
    responses={
        400: {"model": ErrorResponse, "description": "auth.code_invalid"},
        429: {"model": ErrorResponse, "description": "too_many_requests"},
    },
)
async def check_code(body: VerifyRequest, request: Request) -> VerifyResponse:
    return await verify_code(body.email, body.code, get_client_ip(request))


@auth.post(
    "/refresh",
    response_model=TokenPair,
    responses={401: {"model": ErrorResponse, "description": "auth.session_expired"}},
)
async def refresh_session(body: RefreshRequest) -> TokenPair:
    return await rotate_refresh_token(body.refresh_token)


@auth.post("/logout", status_code=204, response_model=None)
async def logout(
    user: Annotated[User, Depends(get_signed_in_user)],
    identity: Annotated[tuple[UUID, UUID], Depends(bearer_identity)],
) -> Response:
    await revoke_sessions(user.id, session_id=identity[1])
    return Response(status_code=204)


@auth.post("/logout-all", status_code=204, response_model=None)
async def logout_all(user: Annotated[User, Depends(get_current_user)]) -> Response:
    await revoke_sessions(user.id)
    return Response(status_code=204)


@account.get("/me", response_model=UserMe)
async def read_me(user: Annotated[User, Depends(get_signed_in_user)]) -> UserMe:
    return user_me(user)
