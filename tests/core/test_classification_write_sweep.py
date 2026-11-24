"""Only src/core/classification.py writes ``organizations.org_kind_id`` (#877).

Sources suggest; an operator decides. The resolved kind is the operator's, so
an applier, importer, observation writer or script that set it directly would
silently overrule them, and no test of that path would notice. This sweep
reads every Python file under ``src/`` and ``scripts/`` and fails on any SQL
that assigns or inserts the column outside the module that owns it.
"""

import re
from pathlib import Path

OWNER = Path("src/core/classification.py")
_WRITES = (
    re.compile(r"\bSET\b[^;]*?\borg_kind_id\s*=", re.IGNORECASE | re.DOTALL),
    re.compile(r"INSERT\s+INTO\s+organizations\s*\([^)]*\borg_kind_id\b", re.IGNORECASE),
)


def _writers(paths) -> list[str]:
    hits = []
    for path in paths:
        if path == OWNER:
            continue
        text = path.read_text()
        if "org_kind_id" in text and any(p.search(text) for p in _WRITES):
            hits.append(str(path))
    return sorted(hits)


def test_only_the_owner_writes_org_kind_id():
    files = [*Path("src").rglob("*.py"), *Path("scripts").rglob("*.py")]
    assert _writers(files) == [], "set org_kind_id through classification.record_decision"


def test_owner_is_the_writer():
    """The owner itself matches, so the patterns aren't vacuous."""
    text = OWNER.read_text()
    assert any(p.search(text) for p in _WRITES)


def test_sweep_catches_a_stray_writer(tmp_path):
    stray = tmp_path / "stray.py"
    stray.write_text('SQL = "UPDATE organizations SET notes=$1, org_kind_id=$2 WHERE id=$3"\n')
    assert _writers([stray]) == [str(stray)]
