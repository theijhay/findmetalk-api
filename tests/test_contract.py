"""The published contract (TR-8) matches the code."""

from typing import Literal

import pytest
from pydantic import BaseModel

from api.contract import events
from api.contract.export import CONTRACT_DIR, contract_files


def test_committed_contract_matches_the_code() -> None:
    stale = [
        name
        for name, content in contract_files().items()
        if not (CONTRACT_DIR / name).exists() or (CONTRACT_DIR / name).read_text(encoding="utf-8") != content
    ]
    assert not stale, f"Run `python -m api.contract.export` and commit: {stale}"


def test_openapi_title_uses_the_configured_product_name() -> None:
    from main import app

    assert app.openapi()["info"]["title"] == "FindMeTalk API"


class _PresenceOpened(BaseModel):
    user_id: str
    intent: Literal["want_to_talk", "happy_to_listen"] | None = None


class _Heartbeat(BaseModel):
    pass


def test_registered_events_are_published_as_versioned_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "SERVER_EVENTS", {"presence.opened": _PresenceOpened})
    monkeypatch.setattr(events, "CLIENT_EVENTS", {"presence.heartbeat": _Heartbeat})

    schema = events.build_event_schema()

    (server,) = schema["properties"]["server_to_client"]["oneOf"]
    assert server["properties"]["event"] == {"const": "presence.opened"}
    assert server["properties"]["v"] == {"const": events.EVENT_SCHEMA_VERSION}
    assert server["properties"]["data"] == {"$ref": "#/$defs/_PresenceOpened"}
    assert set(schema["$defs"]["_PresenceOpened"]["properties"]) == {"user_id", "intent"}

    (client,) = schema["properties"]["client_to_server"]["oneOf"]
    assert client["properties"]["event"] == {"const": "presence.heartbeat"}
