from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy import text

from api.core.cors import ErrorCORSMiddleware
from api.core.errors import ErrorResponse, install_error_handlers
from api.core.health import health
from api.core.middleware import RequestContextMiddleware
from api.core.redis_client import redis_client
from api.db.database import engine
from api.utils.logger import configure_logging, logger
from api.utils.settings import settings
from api.v1.routes import api_version_one

# The published contract version (contract/openapi.json). Bumped by hand on a
# breaking API change, never read from the environment, so every environment
# serves the same contract.
API_VERSION = "0.2.0"

configure_logging(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        # Fail fast on a misconfigured deployment rather than serve requests
        # that can only fail. Outages after startup are reported by /ready.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("PostgreSQL connection OK")

        await redis_client.ping()
        logger.info("Redis connection OK")

        yield
    finally:
        try:
            await redis_client.aclose()
        finally:
            await engine.dispose()


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version=API_VERSION,
    lifespan=lifespan,
    responses={
        "default": {"model": ErrorResponse, "description": "Error response"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
    # The contract is published from the repository (contract/openapi.json),
    # so production does not also serve an interactive explorer.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)
install_error_handlers(app)

app.add_middleware(
    ErrorCORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last so it is outermost: every response, CORS preflight included,
# carries a request id and is logged.
app.add_middleware(RequestContextMiddleware)

app.include_router(health)
app.include_router(api_version_one)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=settings.DEBUG,
        log_config=None,  # keep configure_logging's JSON handler
    )
