from fastapi import APIRouter

from api.v1.routes.auth.auth import account, auth

api_version_one = APIRouter(prefix="/api/v1")
api_version_one.include_router(auth)
api_version_one.include_router(account)
