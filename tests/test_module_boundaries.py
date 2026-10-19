"""The modular monolith's boundaries are enforced, not just described (spec 10.2).

Code lives in `api/v1/<layer>/<module>/`, with layer one of routes, services,
models, schemas and module one of the spec's modules. A module may use another
module only through its service interface (`api.v1.services.<other>`) or its
schemas. Reaching into another module's tables (`api.v1.models.<other>`) or
routes fails this test, so the modules can later be split into services
without untangling shared tables.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
V1 = REPO_ROOT / "api" / "v1"

# Spec 10.2. Adding a module is an architecture change: ask the owner first.
MODULES = frozenset(
    {
        "auth",
        "profiles",
        "presence",
        "calls",
        "circles",
        "safety",
        "recordings",
        "media_tokens",
        "admin",
    }
)
LAYERS = ("routes", "services", "models", "schemas")
# What one module may import from another.
CROSS_MODULE_ALLOWED_LAYERS = frozenset({"services", "schemas"})


def module_of(path: Path) -> tuple[str, str] | None:
    """(layer, module) for a file inside a module folder, else None."""
    rel = path.relative_to(V1).parts
    if len(rel) >= 3 and rel[0] in LAYERS:
        return rel[0], rel[1]
    return None


def imported_names(source: str) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def violations(own_module: str, source: str) -> list[str]:
    found = []
    for name in imported_names(source):
        parts = name.split(".")
        if len(parts) < 4 or parts[:2] != ["api", "v1"]:
            continue
        layer, other = parts[2], parts[3]
        if other in MODULES and other != own_module and layer not in CROSS_MODULE_ALLOWED_LAYERS:
            found.append(name)
    return sorted(set(found))


def _module_files() -> list[Path]:
    return sorted(p for p in V1.rglob("*.py") if module_of(p) is not None)


def test_every_module_folder_is_a_spec_module() -> None:
    unknown = {
        f"api/v1/{layer}/{module}"
        for layer, module in filter(None, (module_of(p) for p in _module_files()))
        if module not in MODULES
    }
    assert not unknown, (
        f"{sorted(unknown)} is not one of the spec's modules {sorted(MODULES)}. "
        "Put the code in the module it belongs to, or ask the owner before adding a module."
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_modules_only_use_each_other_through_services_and_schemas(path: Path) -> None:
    located = module_of(path)
    assert located is not None
    bad = violations(located[1], path.read_text(encoding="utf-8"))
    assert not bad, f"{path.relative_to(REPO_ROOT)} reaches into another module: {bad}"


# The checker itself, so the guard above is known to bite even while the
# module folders are still empty.


def test_checker_flags_another_modules_models_and_routes() -> None:
    source = (
        "from api.v1.models.profiles.profile import Profile\n"
        "import api.v1.routes.circles.circles\n"
        "from api.v1.models import calls\n"
    )
    assert violations("safety", source) == [
        "api.v1.models.calls",
        "api.v1.models.profiles.profile",
        "api.v1.models.profiles.profile.Profile",
        "api.v1.routes.circles.circles",
    ]


def test_checker_allows_services_schemas_own_module_and_shared_code() -> None:
    source = (
        "from api.v1.services.profiles.profile_service import ProfileService\n"
        "from api.v1.schemas.profiles.profile import ProfileCard\n"
        "from api.v1.models.calls.call import Call\n"
        "from api.v1.models.base_model import BaseTableModel\n"
        "from api.core.redis_client import redis_client\n"
    )
    assert violations("calls", source) == []


def test_module_of_ignores_files_outside_module_folders() -> None:
    assert module_of(V1 / "models" / "base_model.py") is None
    assert module_of(V1 / "routes" / "__init__.py") is None
    assert module_of(V1 / "services" / "calls" / "call_service.py") == ("services", "calls")
