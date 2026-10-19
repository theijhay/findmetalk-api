"""Shared test setup.

Settings would otherwise read the developer's `.env`, which may point at a
real database. Environment variables win over `.env`, so they are pinned here,
before anything imports the app:

* Unit tests get URLs on a closed port. A test that accidentally opens a real
  connection fails at once instead of touching anything.
* Integration tests (`tests/integration/`, marker `integration`) use
  TEST_DB_URL and TEST_REDIS_URL, which must point at disposable services:
  `make up` locally (the `findmetalk_test` database), service containers in CI.
  Without them those tests are skipped, unless REQUIRE_INTEGRATION=1 (set in
  CI), which turns the skip into a failure so CI can never pass by skipping.
"""

import os

import pytest

_CLOSED_DB_URL = "postgresql://unused:unused@127.0.0.1:1/unused"
_CLOSED_REDIS_URL = "redis://127.0.0.1:1/0"

TEST_DB_URL = os.environ.get("TEST_DB_URL")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")
INTEGRATION_AVAILABLE = bool(TEST_DB_URL and TEST_REDIS_URL)

os.environ["APP_ENV"] = "test"
os.environ["DB_URL"] = TEST_DB_URL or _CLOSED_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL or _CLOSED_REDIS_URL


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if INTEGRATION_AVAILABLE:
        return
    if os.environ.get("REQUIRE_INTEGRATION") == "1":
        raise pytest.UsageError("REQUIRE_INTEGRATION=1 but TEST_DB_URL / TEST_REDIS_URL are not set.")
    skip = pytest.mark.skip(reason="set TEST_DB_URL and TEST_REDIS_URL (see `make test-integration`)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
