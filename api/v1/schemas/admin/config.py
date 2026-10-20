"""Runtime configuration keys shared through the admin service interface."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictBool

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
    override = value.countries.get(country) if country is not None else None
    default = value.default.call if kind == "call" else value.default.circle
    return override.root.get(kind, default) if override is not None else default
