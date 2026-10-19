"""The versioned WebSocket event schema (TR-8).

Every message the realtime gateway sends a browser is one of the Pydantic
models registered in ``SERVER_EVENTS``, and every message a browser may send
is one in ``CLIENT_EVENTS``. The web and admin repos generate their types from
the exported JSON Schema (``contract/events.schema.json``), so this registry is
the only place an event's shape is defined.

Rules:
- The key is the event name on the wire and never changes once shipped.
  Domain event names in spec 10.6 are the vocabulary (``presence.opened``,
  ``call.requested`` ...).
- A breaking change to an event's payload bumps ``EVENT_SCHEMA_VERSION``.
- Registries start empty: each slice registers its own events (Slice 2 adds
  presence).
"""

from typing import Any

from pydantic import BaseModel

EVENT_SCHEMA_VERSION = 1

SERVER_EVENTS: dict[str, type[BaseModel]] = {}
CLIENT_EVENTS: dict[str, type[BaseModel]] = {}


def build_event_schema() -> dict[str, Any]:
    """One JSON Schema document describing every event in both directions."""
    defs: dict[str, Any] = {}

    def register(direction: str, events: dict[str, type[BaseModel]]) -> list[dict[str, Any]]:
        variants = []
        for name in sorted(events):
            schema = events[name].model_json_schema(ref_template="#/$defs/{model}")
            defs.update(schema.pop("$defs", {}))
            title = schema.get("title", events[name].__name__)
            defs[title] = schema
            variants.append(
                {
                    "type": "object",
                    "required": ["event", "v", "data"],
                    "properties": {
                        "event": {"const": name},
                        "v": {"const": EVENT_SCHEMA_VERSION},
                        "data": {"$ref": f"#/$defs/{title}"},
                    },
                    "additionalProperties": False,
                    "x-direction": direction,
                }
            )
        return variants

    def any_of(variants: list[dict[str, Any]]) -> dict[str, Any]:
        # JSON Schema forbids an empty oneOf; `not: {}` is "no valid message".
        return {"oneOf": variants} if variants else {"not": {}}

    server = register("server_to_client", SERVER_EVENTS)
    client = register("client_to_server", CLIENT_EVENTS)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WebSocketEvents",
        "x-event-schema-version": EVENT_SCHEMA_VERSION,
        "$defs": dict(sorted(defs.items())),
        "properties": {
            "server_to_client": any_of(server),
            "client_to_server": any_of(client),
        },
    }
