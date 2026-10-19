import json
import logging

from api.utils.logger import JsonFormatter, redact, request_id_var


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("findmetalk", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_lines_are_json_with_extras_and_the_request_id() -> None:
    token = request_id_var.set("req-12345678")
    try:
        line = JsonFormatter().format(_record("call ended", call_id="c1", end_reason="hangup_caller"))
    finally:
        request_id_var.reset(token)

    entry = json.loads(line)
    assert entry["msg"] == "call ended"
    assert entry["level"] == "INFO"
    assert entry["request_id"] == "req-12345678"
    assert entry["call_id"] == "c1"
    assert entry["end_reason"] == "hangup_caller"


def test_no_request_id_outside_a_request() -> None:
    entry = json.loads(JsonFormatter().format(_record("worker tick")))
    assert "request_id" not in entry


def test_redact_keeps_only_a_prefix_and_the_length() -> None:
    assert redact("abcdefghijkl") == "abcd…(len=12)"
    assert redact(None) == "<none>"
    assert redact("") == "<none>"


def test_uvicorns_terminal_colour_copy_is_dropped() -> None:
    entry = json.loads(JsonFormatter().format(_record("started", color_message="\x1b[36mstarted\x1b[0m")))
    assert "color_message" not in entry
