"""Public email OTP endpoints (FR-AUTH-1, FR-AUTH-4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from api.core.client_ip import get_client_ip
from api.core.errors import ErrorResponse
from api.v1.schemas.auth.auth import CodeRequest, CodeResponse, VerifyRequest, VerifyResponse
from api.v1.services.auth.email import EmailSender, get_email_sender
from api.v1.services.auth.otp import request_code, verify_code

auth = APIRouter(prefix="/auth", tags=["Auth"])


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
