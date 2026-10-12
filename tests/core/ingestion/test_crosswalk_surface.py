"""The crosswalk's cutover seed is retired (#502); only the applier's half remains.

``scripts/seed_producer_crosswalk.py`` ran once, at the #490 cutover, and the
crosswalk's writer since is the applier (#499). ``scripts/seed_roles.py`` was
superseded by usa-wa's published ``roles`` dataset (#500). This pins the
module's surface to what the applier imports, so the parsing and resolution
machinery the seed needed cannot creep back unused. Pure unit.
"""

from pathlib import Path

from src.core.ingestion import crosswalk

REPO = Path(__file__).resolve().parents[3]


def test_crosswalk_exports_only_the_applier_surface():
    assert sorted(crosswalk.__all__) == [
        "ANCHOR_KINDS",
        "IN_SCOPE",
        "PRODUCER_SOURCE",
        "TOMBSTONE_TYPE",
        "repoint_anchors",
    ]


def test_cutover_seed_scripts_are_gone():
    for name in ("seed_producer_crosswalk.py", "seed_roles.py"):
        assert not (REPO / "scripts" / name).exists(), name
