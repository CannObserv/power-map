"""Route enumeration guards (#772).

FastAPI 0.137 stopped flattening included routers into ``app.routes``. Code
that read ``app.routes`` for ``APIRoute``s then saw none, and nothing failed.
The rate limiter's read-POST set went quietly empty. Three guards:

1. **Pins.** The installed read-POST set and the route count are absolute, so
   an enumeration that comes back empty or partial fails here instead of
   passing vacuously.
2. **The helper's shape.** ``iter_api_routes`` yields the effective route: full
   prefixed path, and the dependant with app-level dependencies merged in.
3. **The sweep.** No module reads ``.routes`` or calls ``iter_route_contexts``
   except ``src/api/routing.py``. Without it, the next reader could go back to
   ``app.routes`` and pass over an empty list again.

The sweep has **no allowlist** apart from the helper itself. The detector
self-tests at the bottom show it a violation it must catch and compliant code it
must not flag.
"""

import ast
from pathlib import Path

import pytest

from src.api.main import app
from src.api.public import ratelimit
from src.api.routing import iter_api_routes
from src.api.validation import reject_unstorable_text

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "src" / "api" / "routing.py"
SWEPT_DIRS = ("src", "tests", "scripts", "clients")

# Every APIRoute the app serves, counting admin, public and probes. A route
# added or removed changes this number; update it in the same diff. Routes under
# ``/_test/`` are left out because test modules register them on ``app`` at
# import (``public/test_require_scope.py``), so counting them would make the
# total depend on which modules ran first.
API_ROUTE_COUNT = 406

# The POST routes marked ``x-rate-limit-bucket: read`` (#310).
READ_POST_PATHS = frozenset(
    {
        "/api/v1/orgs/batch-get",
        "/api/v1/people/embeddings/presence",
        "/api/v1/people/identify",
        "/api/v1/people/identify-batch",
        "/api/v1/people/verify",
        "/api/v1/people/verify-batch",
    }
)


def test_installed_read_post_set_is_the_six_marked_paths():
    """Checks the set ``main`` installed at import, not a re-derivation."""
    assert ratelimit._post_read_paths == READ_POST_PATHS


def test_route_walk_yields_every_api_route():
    served = [r for r in iter_api_routes(app) if "/_test/" not in r.path]
    assert len(served) == API_ROUTE_COUNT


def test_paths_carry_the_router_prefix():
    """An included router's own route has the un-prefixed ``/orgs/...`` path;
    the helper yields the effective one under ``/admin``."""
    paths = {r.path for r in iter_api_routes(app)}
    assert "/admin/orgs/{org_id}/" in paths
    assert "/orgs/{org_id}/" not in paths


def test_dependant_carries_the_app_level_dependency():
    """``reject_unstorable_text`` is declared on ``FastAPI(dependencies=...)``.
    Only the effective route's dependant has it; the router's own lacks it."""
    route = next(r for r in iter_api_routes(app) if r.path == "/admin/orgs/{org_id}/")
    assert route.dependant.dependencies[0].call is reject_unstorable_text


# ---------------------------------------------------------------------------
# Sweep: one reader of the route tree
# ---------------------------------------------------------------------------


def _violations(source: str) -> list[str]:
    """``.routes`` reads and ``iter_route_contexts`` references in ``source``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "routes":
            found.append(f"{node.lineno}: .routes")
        elif isinstance(node, ast.Attribute) and node.attr == "iter_route_contexts":
            found.append(f"{node.lineno}: iter_route_contexts")
        elif isinstance(node, ast.Name) and node.id == "iter_route_contexts":
            found.append(f"{node.lineno}: iter_route_contexts")
        elif isinstance(node, ast.ImportFrom) and any(
            a.name == "iter_route_contexts" for a in node.names
        ):
            found.append(f"{node.lineno}: imports iter_route_contexts")
    return found


def _swept_files() -> list[Path]:
    files = []
    for top in SWEPT_DIRS:
        files += [p for p in (REPO / top).rglob("*.py") if ".venv" not in p.parts]
    return sorted(p for p in files if p != HELPER)


def test_sweep_covers_the_known_readers():
    """Guards the sweep: it would pass vacuously if it walked nothing."""
    swept = set(_swept_files())
    assert REPO / "src" / "api" / "main.py" in swept
    assert REPO / "tests" / "api" / "admin" / "admin_routes.py" in swept
    assert HELPER not in swept


def test_only_the_helper_reads_the_route_tree():
    offenders = {
        str(p.relative_to(REPO)): v
        for p in _swept_files()
        if (v := _violations(p.read_text(encoding="utf-8")))
    }
    assert not offenders, (
        "read routes through src.api.routing.iter_api_routes(app), never app.routes "
        f"or iter_route_contexts (#772): {offenders}"
    )


# Detector self-tests: each violation must be caught, each compliant sample not.


@pytest.mark.parametrize(
    "source",
    [
        "for r in app.routes: pass",
        "x = [r for r in app.router.routes]",
        "n = len(router.routes)",
        "from fastapi.routing import iter_route_contexts",
        "import fastapi.routing\nfastapi.routing.iter_route_contexts(x)",
    ],
    ids=["app-routes", "router-routes", "len-routes", "import-walker", "attr-walker"],
)
def test_detector_flags(source):
    assert _violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from src.api.routing import iter_api_routes\nfor r in iter_api_routes(app): pass",
        "routes = 1\nprint(routes)",
        '"""Derived from ``app.routes``."""',
    ],
    ids=["helper", "plain-name", "docstring"],
)
def test_detector_passes(source):
    assert not _violations(source)
