"""Liveness and readiness probes.

``/health`` answers whenever the process is serving, so the platform does not
restart the API over a dependency outage it cannot fix. ``/ready`` checks
Postgres and Redis, so a load balancer stops sending traffic to an instance
that cannot reach them.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from api.core.redis_client import redis_client
from api.db.database import engine
from api.utils.logger import logger

# Long enough for a healthy managed database across a region, short enough
# that a probe never hangs past the platform's own probe timeout.
CHECK_TIMEOUT_SECONDS = 2.0

health = APIRouter(tags=["Health"])

CheckState = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckState]


async def _check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    await redis_client.ping()


_CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "database": _check_database,
    "redis": _check_redis,
}


async def _run(name: str, check: Callable[[], Awaitable[None]]) -> CheckState:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        # The exception text can carry a connection string; log its type only.
        logger.warning("readiness check failed", extra={"check": name, "error": type(exc).__name__})
        return "unavailable"
    return "ok"


@health.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@health.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readiness_check() -> JSONResponse:
    results = await asyncio.gather(*(_run(name, check) for name, check in _CHECKS.items()))
    checks: dict[str, CheckState] = dict(zip(_CHECKS, results, strict=True))
    ready = all(state == "ok" for state in checks.values())
    body = ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if ready else 503, content=body.model_dump())
