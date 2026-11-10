"""Root-disk hygiene is the cohort's canonical disk GC, not power-map's own (#705).

#665/#690 built ``prune_disk.py`` and ``check_disk_headroom.py`` with their
``power-map-disk-{prune,headroom}`` units. #705 retired both for Provisioner's
GC and headroom sensor (``cannobserv-disk-gc`` / ``cannobserv-disk-headroom``),
installed under ``/usr/local/lib`` through the vendored ``maintaining-host-disk``
skill and configured in ``/etc/cannobserv/disk-gc/disk-gc.conf``; none of it is
tracked here. Bringing either old tool back would run two GCs on one disk, and
the skill's retire check matches these names on the host for that reason. This
guard keeps them out of the repo side too.
"""

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
RETIRED_SCRIPTS = ("prune_disk.py", "check_disk_headroom.py")
RETIRED_UNIT_PREFIX = "power-map-disk-"


def test_no_power_map_disk_units_are_tracked():
    assert sorted(p.name for p in INFRA.glob(f"{RETIRED_UNIT_PREFIX}*")) == []


def test_the_drift_manifest_names_no_power_map_disk_unit():
    rows = tomllib.loads((INFRA / "installed.toml").read_text())["installed"]
    assert [r for r in rows if r.startswith(RETIRED_UNIT_PREFIX)] == []


def test_the_retired_scripts_are_gone():
    assert [s for s in RETIRED_SCRIPTS if (REPO / "scripts" / s).exists()] == []


def test_no_tracked_unit_runs_a_retired_script():
    hits = [
        f"{unit.name}: {script}"
        for unit in sorted(INFRA.glob("*.service"))
        for script in RETIRED_SCRIPTS
        if script in unit.read_text()
    ]
    assert hits == []
