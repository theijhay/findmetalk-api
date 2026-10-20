"""TR-8 and spec 9.6: errors are a translatable, private API contract."""

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.core.cors import ErrorCORSMiddleware
from api.core.errors import install_error_handlers
from api.core.middleware import RequestContextMiddleware
from api.utils.logger import request_id_var
from main import app


class Input(BaseModel):
    display_name: str = Field(max_length=4)
    count: int


class NestedInput(BaseModel):
    people: list[Input]


@pytest.fixture
async def error_client() -> AsyncIterator[httpx.AsyncClient]:
    application = FastAPI()
    install_error_handlers(application)
    application.add_middleware(RequestContextMiddleware)

    @application.post("/validate", response_model=Input)
    async def validate(body: Input, limit: int) -> Input:
        return body

    @application.post("/nested", response_model=NestedInput)
    async def nested(body: NestedInput) -> NestedInput:
        return body

    @application.get("/http/{status}", response_model=None)
    async def http_error(status: int) -> None:
        raise HTTPException(status, headers={"Retry-After": "60"} if status == 429 else None)

    @application.get("/feature", response_model=None)
    async def feature_error() -> None:
        raise HTTPException(401, detail="auth.code_invalid")

    @application.get("/unexpected", response_model=None)
    async def unexpected() -> None:
        raise RuntimeError("private@example.com secret database connection")

    @application.get("/unsafe-detail", response_model=None)
    async def unsafe_detail() -> None:
        raise HTTPException(500, detail="private@example.com secret database connection")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "bad_request"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (408, "bad_request"),
        (409, "conflict"),
        (410, "bad_request"),
        (413, "payload_too_large"),
        (415, "unsupported_media_type"),
        (418, "bad_request"),
        (422, "validation_failed"),
        (429, "too_many_requests"),
        (500, "internal_error"),
        (502, "internal_error"),
        (503, "service_unavailable"),
    ],
)
async def test_http_errors_use_keys_and_matching_request_ids(
    error_client: httpx.AsyncClient, status: int, code: str
) -> None:
    """TR-8: HTTP errors use keys and preserve protocol headers."""
    response = await error_client.get(f"/http/{status}", headers={"X-Request-ID": "web-request-1234"})
    error: dict[str, object] = {"code": code, "request_id": "web-request-1234"}
    if status == 422:
        error["fields"] = []
    assert response.status_code == status
    assert response.json() == {"error": error}
    assert response.headers["x-request-id"] == error["request_id"]
    if status == 429:
        assert response.headers["retry-after"] == "60"


async def test_framework_404_and_405_are_errors() -> None:
    """TR-8: router errors use the same envelope as application errors."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, path, code in [
            ("GET", "/missing", "not_found"),
            ("POST", "/health", "method_not_allowed"),
        ]:
            response = await client.request(method, path)
            assert response.json() == {
                "error": {"code": code, "request_id": response.headers["x-request-id"]}
            }
            if method == "POST":
                assert "GET" in response.headers["allow"]


async def test_validation_reports_locations_and_types_without_input(error_client: httpx.AsyncClient) -> None:
    """TR-8: validation exposes field keys and types, never user input or messages."""
    response = await error_client.post("/validate?limit=private@example.com", json={"display_name": "secret"})
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_failed",
            "request_id": response.headers["x-request-id"],
            "fields": [
                {"field": "limit", "code": "int_parsing"},
                {"field": "display_name", "code": "string_too_long"},
                {"field": "count", "code": "missing"},
            ],
        }
    }
    assert "private@example.com" not in response.text
    assert "secret" not in response.text


async def test_feature_error_keys_are_preserved(error_client: httpx.AsyncClient) -> None:
    """Spec 9.6: a feature can supply its own stable translation key."""
    response = await error_client.get("/feature")
    assert response.json()["error"]["code"] == "auth.code_invalid"


async def test_nested_validation_fields_include_array_indexes(error_client: httpx.AsyncClient) -> None:
    """TR-8: clients can locate invalid fields inside nested arrays."""
    response = await error_client.post("/nested", json={"people": [{"display_name": "ok"}]})
    assert response.json()["error"]["fields"] == [{"field": "people.0.count", "code": "missing"}]


async def test_malformed_json_uses_the_validation_envelope(error_client: httpx.AsyncClient) -> None:
    """TR-8: JSON parsing failures never expose the parser's message or input."""
    response = await error_client.post(
        "/validate?limit=1", content='{"secret":', headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["fields"][0]["code"] == "json_invalid"
    assert "secret" not in response.text


async def test_cors_rejections_use_errors_and_successful_preflights_still_work() -> None:
    """TR-8: CORS failures use keys and keep the browser's policy headers."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "origin": "https://untrusted.example",
                "access-control-request-method": "GET",
            },
        )
        assert response.status_code == 400
        assert response.json() == {
            "error": {"code": "bad_request", "request_id": response.headers["x-request-id"]}
        }
        assert "access-control-allow-origin" not in response.headers
        assert int(response.headers["content-length"]) == len(response.content)
        response = await client.options(
            "/health",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert response.headers["x-request-id"]


async def test_cors_rejection_generates_request_id_when_context_is_unset() -> None:
    """TR-8: CORS rejections stay valid when no request context is available."""
    application = FastAPI()
    application.add_middleware(ErrorCORSMiddleware)
    token = request_id_var.set(None)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.options(
                "/health",
                headers={
                    "origin": "https://untrusted.example",
                    "access-control-request-method": "GET",
                },
            )
    finally:
        request_id_var.reset(token)
    assert response.status_code == 400
    request_id = response.headers["x-request-id"]
    assert UUID(hex=request_id).version == 4
    assert response.json() == {"error": {"code": "bad_request", "request_id": request_id}}


@pytest.mark.parametrize("path", ["/unexpected", "/unsafe-detail"])
async def test_500_hides_exception_text_and_keeps_request_context(
    error_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    """TR-8: 500s hide internals in responses and logs and retain their request id."""
    with caplog.at_level(logging.INFO, logger="findmetalk"):
        response = await error_client.get(path)
    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.json() == {"error": {"code": "internal_error", "request_id": request_id}}
    rendered = json.dumps([record.__dict__ for record in caplog.records], default=str)
    assert "private@example.com" not in response.text + rendered
    assert "secret" not in response.text + rendered
    (access,) = [record for record in caplog.records if record.getMessage() == "request"]
    assert access.__dict__["status"] == 500


def test_every_route_publishes_the_default_error_schema() -> None:
    """TR-8: every published operation describes the shared error model."""
    schema = app.openapi()
    assert "ErrorResponse" in schema["components"]["schemas"]
    for path in schema["paths"].values():
        for operation in path.values():
            for status, response in operation["responses"].items():
                if status == "default" or int(status) >= 400:
                    assert response["content"]["application/json"]["schema"] == {
                        "$ref": "#/components/schemas/ErrorResponse"
                    }
            assert "default" in operation["responses"]
