"""Write the API contract to ``contract/`` or check it is up to date (TR-8).

    python -m api.contract.export          # regenerate after changing the API
    python -m api.contract.export --check  # CI: fail if the committed files drifted

The committed files are what the web and admin repos generate their types
from, so a route or event change that is not re-exported is a broken
contract, and CI fails the pull request.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from api.contract.events import build_event_schema

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contract"


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def contract_files() -> dict[str, str]:
    # Imported here so `--help` works without the app's required settings.
    from main import app

    return {
        "openapi.json": _render(app.openapi()),
        "events.schema.json": _render(build_event_schema()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="fail if the committed contract is stale")
    args = parser.parse_args(argv)

    stale = []
    for name, content in contract_files().items():
        path = CONTRACT_DIR / name
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(name)
        else:
            CONTRACT_DIR.mkdir(exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote contract/{name}")

    if stale:
        print(
            "The committed API contract is out of date: "
            + ", ".join(f"contract/{name}" for name in stale)
            + ". Run `python -m api.contract.export` and commit the result.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
