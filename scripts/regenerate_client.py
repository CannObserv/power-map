"""Regenerate ``clients/python`` from the app's OpenAPI schema (#618).

Writes three things, all derived and all reviewed in the same PR as the change
that caused them:

1. ``clients/python/openapi.json``: ``app.openapi()``, built in process (no
   server, no DB).
2. ``clients/python/src/power_map_client/generated/``: ``openapi-python-client``
   output from that snapshot. It is generated into a staging directory and then
   swapped in, so a failed generation leaves the committed tree alone.
3. The client's ``[project].version``, set to the app's.

``tests/clients/python/test_drift.py`` fails until this has been run after any
change to the public schema or the version.

Usage:
    uv run python -m scripts.regenerate_client
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from src.api.health import APP_VERSION
from src.api.main import app

ROOT = Path(__file__).resolve().parents[1]
SDK_DIR = ROOT / "clients" / "python"
SNAPSHOT = SDK_DIR / "openapi.json"
CONFIG = SDK_DIR / "openapi-python-client.yaml"
GENERATED_DIR = SDK_DIR / "src" / "power_map_client" / "generated"
# The generator's console script, from the same environment as this interpreter.
GENERATOR = Path(sys.executable).parent / "openapi-python-client"

_VERSION_LINE = re.compile(r'^version = "[^"]*"$', re.MULTILINE)


def render_schema(schema: dict[str, Any]) -> str:
    """The snapshot's exact bytes for a schema: stable, reviewable JSON."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def generate(snapshot: Path, out: Path) -> None:
    """Run ``openapi-python-client`` on ``snapshot``, writing the package to ``out``.

    The generator's post-hooks call ``ruff`` from ``PATH``; this environment's
    ``bin`` goes first so they use the locked ruff, however this was launched.
    """
    path = os.pathsep.join([str(GENERATOR.parent), os.environ.get("PATH", "")])
    subprocess.run(
        [
            str(GENERATOR),
            "generate",
            "--path",
            str(snapshot),
            "--meta",
            "none",
            "--config",
            str(CONFIG),
            "--output-path",
            str(out),
            "--overwrite",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )


def stamp_version(pyproject: Path, version: str) -> None:
    """Set ``[project].version`` in the client's ``pyproject.toml``."""
    text = pyproject.read_text()
    stamped, n = _VERSION_LINE.subn(f'version = "{version}"', text, count=1)
    if n != 1:
        raise ValueError(f"no top-level version line in {pyproject}")
    pyproject.write_text(stamped)


def main() -> None:
    """Rewrite the snapshot, the generated tree and the client version."""
    SNAPSHOT.write_text(render_schema(app.openapi()))
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "generated"
        generate(SNAPSHOT, staged)
        shutil.rmtree(GENERATED_DIR, ignore_errors=True)
        shutil.copytree(staged, GENERATED_DIR)
    stamp_version(SDK_DIR / "pyproject.toml", APP_VERSION)
    print(f"regenerated clients/python at {APP_VERSION}: review `git diff -- clients/python`")


if __name__ == "__main__":
    main()
