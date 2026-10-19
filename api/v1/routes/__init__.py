from fastapi import APIRouter

api_version_one = APIRouter(prefix="/api/v1")

# Each module's router is included here as its slice lands, for example:
#   from api.v1.routes.auth.auth import auth
#   api_version_one.include_router(auth)
