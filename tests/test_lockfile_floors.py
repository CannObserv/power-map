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
    # The four Dependabot security PRs opened before any dependabot.yml (#715).
    # API runtime, via email-validator (which never resolves DNS here).
    "dnspython": ("2.9.0", "GHSA-45c3-73f7-pv4m, #713/#715"),
    # The mapping build, via dbt's mashumaro[msgpack] (partial_parse.msgpack).
    "msgpack": ("1.2.3", "GHSA-j586-36cw-2gc2, #714/#715"),
    # The commit hooks, via pre-commit → virtualenv / python-discovery. No 3.x
    # release carries the fix: 4.2.1 is the first outside every open range.
    "filelock": ("4.2.1", "GHSA-69hj-f68j-2879 and 19 more, #699/#715"),
    # Needs packaging>=26.3 from 21.14.6 on; a re-lock that holds packaging
    # back resolves 21.14.5, still vulnerable.
    "virtualenv": ("21.14.13", "GHSA-cx6f-vjhg-g3cc and 19 more, #700/#715"),
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
