"""Probes and the request context every response carries."""

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from api.core import health as health_module
from main import app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # No lifespan: unit tests never reach Postgres or Redis.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("postgresql://user:secret@db/findmetalk refused")


async def test_health_answers_without_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_every_dependency_answers(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(health_module._CHECKS, "database", _ok)
    monkeypatch.setitem(health_module._CHECKS, "redis", _ok)

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


async def test_not_ready_logs_the_failed_dependency_without_exposing_its_error(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """TR-8: readiness logs identify the failed dependency without leaking credentials."""
    monkeypatch.setitem(health_module._CHECKS, "database", _down)
    monkeypatch.setitem(health_module._CHECKS, "redis", _ok)

    with caplog.at_level(logging.WARNING, logger="findmetalk"):
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "request_id": response.headers["x-request-id"]},
    }
    (warning,) = [record for record in caplog.records if record.getMessage() == "readiness check failed"]
    assert warning.__dict__["check"] == "database"
    # The connection error carried a credential; neither the body nor the
    # log may repeat it.
    assert "secret" not in response.text
    assert "secret" not in caplog.text


async def test_a_hung_dependency_reports_unavailable_instead_of_hanging(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def _hang() -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(health_module, "CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(health_module._CHECKS, "database", _ok)
    monkeypatch.setitem(health_module._CHECKS, "redis", _hang)

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


async def test_every_response_carries_a_generated_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32


async def test_a_plain_caller_request_id_is_kept(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"x-request-id": "web-abc123-def456"})
    assert response.headers["x-request-id"] == "web-abc123-def456"


@pytest.mark.parametrize("unsafe", ["short", "has spaces in it ok", "<script>alert(1)</script>", "x" * 65])
async def test_an_unsafe_caller_request_id_is_replaced(client: httpx.AsyncClient, unsafe: str) -> None:
    response = await client.get("/health", headers={"x-request-id": unsafe})
    assert response.headers["x-request-id"] != unsafe
    assert len(response.headers["x-request-id"]) == 32


async def test_access_log_records_the_route_never_the_query_or_an_unknown_path(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="findmetalk"):
        await client.get("/health?email=someone@example.com")
        await client.get("/circles/join/SECRETINVITECODE")

    records = [r for r in caplog.records if r.getMessage() == "request"]
    assert [(r.__dict__["route"], r.__dict__["status"]) for r in records] == [
        ("/health", 200),
        ("<unmatched>", 404),
    ]
    rendered = json.dumps([r.__dict__ for r in records], default=str)
    assert "someone@example.com" not in rendered
    assert "SECRETINVITECODE" not in rendered


async def test_plain_starlette_routes_are_logged_by_their_template(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="findmetalk"):
        await client.get("/docs")

    (record,) = [r for r in caplog.records if r.getMessage() == "request"]
    assert (record.__dict__["route"], record.__dict__["status"]) == ("/docs", 200)
