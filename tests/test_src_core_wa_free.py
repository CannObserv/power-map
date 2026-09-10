"""`src/core` carries no WA / usa-wa knowledge (#497, design § src/core goes WA-free).

The mapping project under `src/core/ingestion/mapping/` is the *only* place
usa-wa's ontology lives in PM, and the ingestion tree as a whole is the
subscriber seam — it may name its producer. The domain layer may not: a
producer-specific branch there is the leak #490 names as the cause of the
dual-master defect, and `role_title.py` was its last instance.

Deliberately **no allowlist**, in the `test_dsn_sweep.py` idiom: an exemption
list is a place for a leak to hide. A file that genuinely must name WA belongs
under `ingestion/`, or the fix is to change this test with a reason in the
diff. `schema.sql` is out of scope by construction — it is not a `.py` file,
and its WA content is PM's own reference data (`state_senator`, `wa_pdc`),
with the same status as every other seeded vocabulary.

The detector self-tests at the bottom keep the sweep honest: a walk that
scanned nothing, or a pattern that matched nothing, would look identical to a
clean tree from the outside.
"""

import re
from pathlib import Path

import pytest

SRC_CORE = Path(__file__).resolve().parents[1] / "src" / "core"
EXEMPT = SRC_CORE / "ingestion"

# The producer's names for itself and its sources, plus the state — `\bWA\b`
# so "SWAP" and "Hawaii" pass and " WA " does not.
WA_TOKENS = re.compile(r"usa[-_]wa|wa_pdc|Washington|\bWA\b")


def core_files() -> list[Path]:
    """Every .py under src/core except the ingestion tree."""
    return sorted(
        p for p in SRC_CORE.rglob("*.py") if EXEMPT not in p.parents and p.parent != EXEMPT
    )


def offending_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if WA_TOKENS.search(line)]


def test_src_core_is_wa_free():
    hits = {
        str(p.relative_to(SRC_CORE.parent.parent)): lines
        for p in core_files()
        if (lines := offending_lines(p.read_text()))
    }
    assert hits == {}, (
        "WA / usa-wa knowledge under src/core outside ingestion/ — move it into the "
        f"mapping project or reword it: {hits}"
    )


# --- detector self-tests ------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "from usa-wa's roster",
        "slug = 'usa_wa_house'",
        "wa_pdc ids",
        "Washington State",
        "the WA House",
    ],
)
def test_detector_flags_each_token(line):
    assert offending_lines(line) == [1]


@pytest.mark.parametrize("line", ["Hawaii", "SWAP the rows", "a warning", "WAR and peace"])
def test_detector_ignores_lookalikes(line):
    assert offending_lines(line) == []


def test_the_walk_reaches_the_domain_layer():
    names = {p.name for p in core_files()}
    assert "observation.py" in names and "db.py" in names


def test_the_ingestion_tree_is_exempt_by_construction():
    """datasets.py names the producer on purpose; a walk that flagged it would be wrong."""
    assert (EXEMPT / "datasets.py").exists()
    assert all(EXEMPT not in p.parents for p in core_files())
