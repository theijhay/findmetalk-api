"""`make audit` audits the installed environment; these prove the pin check
that makes that equivalent to auditing the requirement files."""

from importlib import metadata
from pathlib import Path

import pytest

from scripts import check_pins


def test_pins_are_read_with_extras_comments_and_includes(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("fastapi==1.2.3\nuvicorn[standard]==0.5.0  # server\n")
    (tmp_path / "dev.txt").write_text("-r base.txt\n\n# tools\nPyTest_Asyncio==9.0\n")

    assert check_pins.read_pins(tmp_path / "dev.txt") == {
        "fastapi": "1.2.3",
        "uvicorn": "0.5.0",
        "pytest-asyncio": "9.0",
    }


@pytest.mark.parametrize("line", ["alembic", "redis>=5", "httpx~=0.28"])
def test_an_unpinned_requirement_is_refused(tmp_path: Path, line: str) -> None:
    (tmp_path / "r.txt").write_text(line + "\n")
    with pytest.raises(ValueError, match="not pinned"):
        check_pins.read_pins(tmp_path / "r.txt")


def test_a_wrong_or_missing_version_is_reported() -> None:
    installed = metadata.version("pytest")
    problems = check_pins.mismatches(
        {"pytest": installed, "httpx": "0.0.1", "definitely-not-installed-pkg": "1.0"}
    )
    assert problems == [
        "definitely-not-installed-pkg==1.0 is not installed",
        "httpx is " + metadata.version("httpx") + ", pinned 0.0.1",
    ]


def test_this_repository_is_fully_pinned() -> None:
    root = Path(__file__).resolve().parent.parent
    pins = check_pins.read_pins(root / "requirements-dev.txt")
    assert {"fastapi", "pytest", "pip-audit"} <= pins.keys()
