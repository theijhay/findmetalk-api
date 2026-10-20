"""Runtime configuration keys shared through the admin service interface."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, StrictBool

RecordingKind = Literal["call", "circle"]


class ConfigKey(StrEnum):
    RING_TIMEOUT_SECONDS = "calls.ring_timeout_seconds"
    RECALL_COOLDOWN_SECONDS = "calls.recall_cooldown_seconds"
    REQUEST_BURST_PER_HOUR = "calls.request_burst_per_hour"
    MAX_DURATION_SECONDS = "calls.max_duration_seconds"
    RECONNECT_WINDOW_SECONDS = "calls.reconnect_window_seconds"
    HEARTBEAT_TTL_SECONDS = "presence.heartbeat_ttl_seconds"
    CIRCLE_MAX_SIZE = "circles.max_size"
    AUTOFLAG_REPORTER_THRESHOLD = "safety.autoflag_reporter_threshold"
    AUTOFLAG_WINDOW_HOURS = "safety.autoflag_window_hours"
    RECORDING_RETENTION_DAYS = "recordings.retention_days"
    RECORDINGS_ENABLED = "recordings.enabled"
    RULES_VERSION = "consent.rules_version"
    RECORDING_NOTICE_VERSION = "consent.recording_notice_version"


# Runtime values live in Postgres; these are the fallback values for missing
# rows, not environment overrides. Keep the cache within the update window.
CONFIG_CACHE_TTL_SECONDS = 30
RUNTIME_CONFIG_DEFAULTS: Mapping[ConfigKey, JsonValue] = MappingProxyType(
    {
        ConfigKey.RING_TIMEOUT_SECONDS: 20,
        ConfigKey.RECALL_COOLDOWN_SECONDS: 3600,
        ConfigKey.REQUEST_BURST_PER_HOUR: 30,
        ConfigKey.MAX_DURATION_SECONDS: 21600,
        ConfigKey.RECONNECT_WINDOW_SECONDS: 15,
        ConfigKey.HEARTBEAT_TTL_SECONDS: 30,
        ConfigKey.CIRCLE_MAX_SIZE: 8,
        ConfigKey.AUTOFLAG_REPORTER_THRESHOLD: 3,
        ConfigKey.AUTOFLAG_WINDOW_HOURS: 24,
        ConfigKey.RECORDING_RETENTION_DAYS: 14,
        ConfigKey.RECORDINGS_ENABLED: {"default": {"call": True, "circle": True}, "countries": {}},
        ConfigKey.RULES_VERSION: "1",
        ConfigKey.RECORDING_NOTICE_VERSION: "1",
    }
)
RUNTIME_CONFIG_INTEGER_RANGES: Mapping[ConfigKey, tuple[int, int]] = MappingProxyType(
    {
        ConfigKey.RING_TIMEOUT_SECONDS: (5, 60),
        ConfigKey.RECALL_COOLDOWN_SECONDS: (0, 604800),
        ConfigKey.REQUEST_BURST_PER_HOUR: (1, 1000),
        ConfigKey.MAX_DURATION_SECONDS: (600, 86400),
        ConfigKey.RECONNECT_WINDOW_SECONDS: (5, 120),
        ConfigKey.HEARTBEAT_TTL_SECONDS: (10, 120),
        ConfigKey.CIRCLE_MAX_SIZE: (3, 50),
        ConfigKey.AUTOFLAG_REPORTER_THRESHOLD: (2, 50),
        ConfigKey.AUTOFLAG_WINDOW_HOURS: (1, 720),
        ConfigKey.RECORDING_RETENTION_DAYS: (1, 365),
    }
)


class RecordingDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    call: StrictBool
    circle: StrictBool


class RecordingOverride(RootModel[Annotated[dict[RecordingKind, StrictBool], Field(min_length=1)]]):
    pass


class RecordingSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    default: RecordingDefaults
    countries: dict[Annotated[str, Field(pattern=r"^[A-Z]{2}$")], RecordingOverride]


def recording_enabled(value: RecordingSwitches, *, country: str | None, kind: RecordingKind) -> bool:
    override = value.countries.get(country.upper()) if country is not None else None
    default = value.default.call if kind == "call" else value.default.circle
    return override.root.get(kind, default) if override is not None else default
