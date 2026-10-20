"""FR-CALL-5 and REC-8: strict runtime values and recording switch resolution."""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.core.actor import SYSTEM, Actor
from api.v1.schemas.admin.config import ConfigKey, RecordingSwitches, recording_enabled
from api.v1.services.admin.config import validate_config_value

INTEGER_CASES = [
    (ConfigKey.RING_TIMEOUT_SECONDS, 20, 5, 60),
    (ConfigKey.RECALL_COOLDOWN_SECONDS, 3600, 0, 604800),
    (ConfigKey.REQUEST_BURST_PER_HOUR, 30, 1, 1000),
    (ConfigKey.MAX_DURATION_SECONDS, 21600, 600, 86400),
    (ConfigKey.RECONNECT_WINDOW_SECONDS, 15, 5, 120),
    (ConfigKey.HEARTBEAT_TTL_SECONDS, 30, 10, 120),
    (ConfigKey.CIRCLE_MAX_SIZE, 8, 3, 50),
    (ConfigKey.AUTOFLAG_REPORTER_THRESHOLD, 3, 2, 50),
    (ConfigKey.AUTOFLAG_WINDOW_HOURS, 24, 1, 720),
    (ConfigKey.RECORDING_RETENTION_DAYS, 14, 1, 365),
]


@pytest.mark.parametrize(("key", "default", "minimum", "maximum"), INTEGER_CASES)
def test_integer_config_enforces_exact_types_and_inclusive_ranges(
    key: ConfigKey, default: int, minimum: int, maximum: int
) -> None:
    """FR-CALL-5, REC-8: every numeric threshold enforces the issue's type and bounds."""
    for value in (default, minimum, maximum):
        assert validate_config_value(key, value) == value
    invalid: Any
    for invalid in (minimum - 1, maximum + 1, True, False, str(default), float(default), None, [], {}):
        with pytest.raises(ValueError):
            validate_config_value(key, invalid)


@pytest.mark.parametrize("key", [ConfigKey.RULES_VERSION, ConfigKey.RECORDING_NOTICE_VERSION])
def test_consent_versions_remain_strings(key: ConfigKey) -> None:
    """REC-8: consent version identifiers are stored without numeric coercion."""
    assert validate_config_value(key, "1") == "1"
    assert validate_config_value(key, "2026-10") == "2026-10"
    invalid: Any
    for invalid in (1, True, None, {}, []):
        with pytest.raises(ValueError):
            validate_config_value(key, invalid)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        {"default": {"call": True}, "countries": {}},
        {"default": {"call": True, "circle": "true"}, "countries": {}},
        {"default": {"call": 1, "circle": True}, "countries": {}},
        {"default": {"call": True, "circle": True, "video": False}, "countries": {}},
        {
            "default": {"call": True, "circle": True},
            "countries": [],
        },
        {"default": {"call": True, "circle": True}, "countries": {"ng": {"call": False}}},
        {"default": {"call": True, "circle": True}, "countries": {"NGA": {"call": False}}},
        {"default": {"call": True, "circle": True}, "countries": {"NG": {}}},
        {"default": {"call": True, "circle": True}, "countries": {"NG": {"call": None}}},
        {"default": {"call": True, "circle": True}, "countries": {"NG": {"call": "false"}}},
        {"default": {"call": True, "circle": True}, "countries": {"NG": {"circle": 0}}},
        {"default": {"call": True, "circle": True}, "countries": {"NG": {"video": False}}},
        {"default": {"call": True, "circle": True}, "countries": {}, "enabled": True},
    ],
)
def test_recording_switches_refuse_malformed_or_ambiguous_objects(invalid: Any) -> None:
    """REC-8: recording policy refuses coercion, unknown fields and malformed country overrides."""
    with pytest.raises(ValidationError):
        validate_config_value(ConfigKey.RECORDINGS_ENABLED, invalid)


def test_recording_country_overrides_inherit_only_omitted_fields() -> None:
    """REC-8: explicit false overrides the default; absent overrides inherit per call type."""
    value = RecordingSwitches.model_validate(
        {
            "default": {"call": True, "circle": False},
            "countries": {"NG": {"call": False}, "FR": {"call": False, "circle": True}},
        }
    )
    assert recording_enabled(value, country="NG", kind="call") is False
    assert recording_enabled(value, country="NG", kind="circle") is False
    assert recording_enabled(value, country="FR", kind="circle") is True
    assert recording_enabled(value, country="FR", kind="call") is False
    assert recording_enabled(value, country="GB", kind="call") is True
    assert recording_enabled(value, country=None, kind="call") is True
    assert recording_enabled(value, country=None, kind="circle") is False


@pytest.mark.parametrize("actor_type", ["user", "moderator"])
def test_human_actors_require_uuid_ids(actor_type: Any) -> None:
    """FR-CALL-5: durable changes attribute human actors by UUID."""
    identity = uuid4()
    assert Actor(type=actor_type, id=identity).id == identity
    for invalid_id in (None, str(identity), 1):
        invalid: Any = invalid_id
        with pytest.raises(ValueError, match=r"actor\.invalid"):
            Actor(type=actor_type, id=invalid)


def test_system_actor_has_no_user_identity_and_unknown_actor_types_are_refused() -> None:
    """FR-CALL-5: background changes use SYSTEM; unsupported actor types are invalid."""
    assert SYSTEM.type == "system"
    assert SYSTEM.id is None
    with pytest.raises(ValueError, match=r"actor\.invalid"):
        Actor(type="system", id=uuid4())
    invalid: Any
    for invalid in ("admin", "owner", "", None, []):
        invalid_type: Any = invalid
        with pytest.raises(ValueError, match=r"actor\.invalid"):
            Actor(type=invalid_type, id=uuid4())
