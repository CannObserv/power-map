"""The committed client is the published schema, generated (#618).

The gate for spec/client alignment. ``clients/python/openapi.json`` is the
committed snapshot of ``app.openapi()``, and ``power_map_client/generated/`` is
what ``openapi-python-client`` makes of it. A byte diff decides, so *any* change
to the public schema fails here until ``uv run python -m
scripts.regenerate_client`` has been run and its diff committed with the change.
In-process: no network, no DB.
"""

import json
import tomllib
from pathlib import Path

from scripts.regenerate_client import (
    GENERATED_DIR,
    SDK_DIR,
    SNAPSHOT,
    generate,
    render_schema,
)
from src.api.health import APP_VERSION
from src.api.main import app

REGEN = "uv run python -m scripts.regenerate_client"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_snapshot_is_the_published_schema():
    assert SNAPSHOT.read_text() == render_schema(app.openapi()), (
        f"the public schema changed: run `{REGEN}` and commit the diff"
    )


def test_generated_tree_is_the_snapshot_generated(tmp_path):
    fresh = tmp_path / "generated"
    generate(SNAPSHOT, fresh)
    committed, expected = _tree(GENERATED_DIR), _tree(fresh)
    stale = sorted(
        name
        for name in committed.keys() | expected.keys()
        if committed.get(name) != expected.get(name)
    )
    assert not stale, f"generated/ is stale or hand-edited ({stale[:5]}…): run `{REGEN}`"


def test_client_version_is_the_app_version():
    """One version: the client generated from server X.Y.Z is X.Y.Z, tag vX.Y.Z."""
    project = tomllib.loads((SDK_DIR / "pyproject.toml").read_text())["project"]
    assert project["version"] == APP_VERSION, f"run `{REGEN}` after a version bump"


def test_snapshot_carries_the_app_version():
    """After a bump, say *why* the snapshot test fails: the version moved."""
    committed = json.loads(SNAPSHOT.read_text())["info"]["version"]
    assert committed == APP_VERSION, (
        f"snapshot is at {committed}, the app at {APP_VERSION}: run `{REGEN}`"
    )
