"""Security floors on resolved dependencies (#698).

A floor here is a version the lockfile must never resolve below, because the
releases under it carry a known vulnerability. The resolver knows nothing of
advisories, and ``pyproject.toml`` constrains none of these packages (they
arrive transitively), so a later re-lock (a downgrade forced by another
package, a hand-resolved conflict) could quietly bring a bad release back.
These tests read ``uv.lock`` itself, the file ``ExecStartPre``'s ``uv sync``
installs from.
"""

import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

LOCKFILE = Path(__file__).resolve().parents[1] / "uv.lock"

# package -> (minimum version, why)
FLOORS = {
    # PYSEC-2026-4320 / TS-2026-013: compromised emSign/eMudhra intermediates.
    # httpx and requests verify TLS against certifi's bundle, not the system store.
    "certifi": ("2026.10.19", "PYSEC-2026-4320, #698"),
}


def _locked_versions() -> dict[str, list[str]]:
    """Every version ``uv.lock`` resolves, by package name."""
    versions: dict[str, list[str]] = {}
    for package in tomllib.loads(LOCKFILE.read_text())["package"]:
        versions.setdefault(package["name"], []).append(package["version"])
    return versions


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_lockfile_resolves_at_or_above_the_floor(name):
    floor, why = FLOORS[name]
    locked = _locked_versions().get(name)
    assert locked, f"{name} is no longer in uv.lock: drop its floor or find where it went"
    below = [v for v in locked if Version(v) < Version(floor)]
    assert not below, (
        f"uv.lock resolves {name} {', '.join(below)}, below the {floor} floor ({why}): "
        f"run `uv lock --upgrade-package {name}`"
    )
