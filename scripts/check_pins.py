"""Fail unless the installed packages are exactly the pinned ones.

`make audit` audits the installed environment instead of downloading every
requirement again into a throwaway one. That is only as good as the
environment matching `requirements.txt` and `requirements-dev.txt`, which this
checks: every pinned package must be installed at exactly its pinned version.

    python scripts/check_pins.py requirements.txt requirements-dev.txt
"""

import re
import sys
from importlib import metadata
from pathlib import Path

# `name==version` with optional extras: `uvicorn[standard]==0.54.0`.
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def read_pins(path: Path) -> dict[str, str]:
    """Pinned versions from a requirements file, following `-r` includes."""
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            pins.update(read_pins(path.parent / line[3:].strip()))
            continue
        match = _PIN.match(line)
        if match is None:
            raise ValueError(f"{path}: not pinned with ==: {raw.strip()}")
        pins[_normalise(match.group(1))] = match.group(2)
    return pins


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def mismatches(pins: dict[str, str]) -> list[str]:
    problems = []
    for name, wanted in sorted(pins.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{name}=={wanted} is not installed")
            continue
        if installed != wanted:
            problems.append(f"{name} is {installed}, pinned {wanted}")
    return problems


def main(paths: list[str]) -> int:
    pins: dict[str, str] = {}
    for path in paths:
        pins.update(read_pins(Path(path)))
    problems = mismatches(pins)
    if problems:
        print("The environment does not match the pins. Run `make install`.", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"{len(pins)} pinned packages installed at their pinned versions.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["requirements.txt", "requirements-dev.txt"]))
