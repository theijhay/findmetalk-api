"""The API against real Postgres and Redis.

Unit tests fake both, which proves nothing about SQL or Redis semantics. These
run against disposable services (CI service containers, or `make up`) and are
the base the call and circle lifecycle tests (spec 16.1) build on.
"""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from api.core.redis_client import redis_client
from api.db.database import engine
from main import app

pytestmark = pytest.mark.integration


async def test_app_starts_and_reports_ready_against_real_services() -> None:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_postgres_is_the_version_production_runs() -> None:
    async with engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
    # compose.yaml and CI both pin Postgres 16; DigitalOcean managed Postgres
    # must be created on 16 as well.
    assert int(version) // 10000 == 16


async def test_redis_callee_lock_lets_exactly_one_caller_win() -> None:
    """TR-3 / FR-CALL-3: the primitive the call-request lock is built on.

    Twenty concurrent `SET NX PX` attempts on one callee key: exactly one
    succeeds, and the key expires on its own so a crashed request cannot hold
    the callee forever.
    """
    key = f"test:lock:callee:{uuid.uuid4()}"
    try:
        results = await asyncio.gather(
            *(redis_client.set(key, f"caller-{i}", nx=True, px=2000) for i in range(20))
        )
        assert sum(1 for r in results if r) == 1
        ttl = await redis_client.pttl(key)
        assert 0 < ttl <= 2000
    finally:
        await redis_client.delete(key)
