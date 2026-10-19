"""Nothing live names the retired outbox, its stamp, or its prune (#651).

``GET /changes`` retired in #502, and #651 dropped what fed it: the outbox
table, its two writer functions and seven row triggers, the stamped-transaction
helper with the GUC it set (#491), and the outbox leg of the prune (whose
script became ``scripts/prune.py``). :data:`RETIRED` spells each name.
A live mention left behind is a claim about a table that is gone: a test that
counts its rows, a doc that sends a reader to it, a comment that explains a
trigger by what it no longer feeds.

Scope is every regular file git tracks (or would track), this one included,
with three exemptions:

- **History.** ``docs/plans/`` and ``docs/archive/`` record what was decided and
  done; ``.skills/context-metrics.jsonl`` is append-only telemetry, and the
  ``.skills/context-*-ok`` warrant files are the judgements past curation runs
  made about lines as they stood then (an entry for a line that has since
  changed is inert, ``docs/CONTEXT.md``). Rewriting them would falsify the
  record.
- **The drop itself.** ``schema.sql``'s retirement block has to name what it
  drops. Only the text between its two marker lines is exempt, so the rest of
  the file is still scanned.
- **The test that proves the drop.** ``tests/core/test_schema_outbox_retired.py``
  rebuilds the legacy outbox to show ``apply_schema`` removes it.

The detector self-tests keep the sweep honest: a walk that scanned nothing, or
a block marker that matched nothing, would look like a clean tree.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = "src/core/schema.sql"

#: Assembled at runtime so this file's own source never spells a token whole —
#: which is what lets the sweep scan this file like any other.
RETIRED = re.compile(
    "|".join(
        [
            "entity" + "_changes",
            "stamped" + "_transaction",
            r"app\." + "source_key_id",
            "prune" + "_outbox",
            "fn_record_" + r"(?:deleted_)?entity_change",
        ]
    )
)

EXEMPT_PREFIXES = ("docs/plans/", "docs/archive/")
EXEMPT_FILES = frozenset(
    {
        ".skills/context-metrics.jsonl",
        ".skills/context-claims-ok",
        ".skills/context-loss-ok",
        ".skills/context-seams-ok",
        "tests/core/test_schema_outbox_retired.py",
    }
)

BLOCK_START = "-- >>> #651 outbox retirement"
BLOCK_END = "-- <<< #651 outbox retirement"


def _candidate_files() -> list[str]:
    """Repo-relative paths git tracks or would track (submodules and symlinks excluded)."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    rels = sorted({p for p in out.stdout.decode().split("\0") if p})
    return [r for r in rels if (REPO_ROOT / r).is_file() and not (REPO_ROOT / r).is_symlink()]


def _exempt(rel: str) -> bool:
    return rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES)


def strip_retirement_block(text: str) -> str:
    """``text`` without the lines between the #651 markers (markers included)."""
    kept, inside = [], False
    for line in text.splitlines():
        if line.startswith(BLOCK_START):
            inside = True
        if not inside:
            kept.append(line)
        if line.startswith(BLOCK_END):
            inside = False
    return "\n".join(kept)


def offending_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if RETIRED.search(line)]


def _read(rel: str) -> str | None:
    try:
        text = (REPO_ROOT / rel).read_text()
    except UnicodeDecodeError:
        return None
    return strip_retirement_block(text) if rel == SCHEMA_SQL else text


def test_no_live_reference_to_the_retired_outbox():
    hits = {}
    for rel in _candidate_files():
        if _exempt(rel) or (text := _read(rel)) is None:
            continue
        if lines := offending_lines(text):
            hits[rel] = lines
    assert hits == {}, f"live references to the retired outbox (#651): {hits}"


# --- detector self-tests ------------------------------------------------------


def test_the_walk_reaches_the_tree():
    files = _candidate_files()
    assert SCHEMA_SQL in files and "AGENTS.md" in files and len(files) > 500


def test_schema_sql_carries_exactly_one_retirement_block():
    text = (REPO_ROOT / SCHEMA_SQL).read_text()
    assert text.count(BLOCK_START) == 1 and text.count(BLOCK_END) == 1
    block = text[text.index(BLOCK_START) : text.index(BLOCK_END)]
    assert RETIRED.search(block), "the retirement block names nothing it drops"


def test_the_detector_is_live():
    token = "entity" + "_changes"
    assert offending_lines(f"ok\nSELECT 1 FROM {token}\n") == [2]
    assert offending_lines("SELECT set_config('app." + "source_key_id', $1, true)") == [1]
    assert offending_lines("updated_at bumps the parent; changes() is fine") == []


def test_the_block_strip_keeps_text_outside_the_markers():
    token = "entity" + "_changes"
    text = f"a\n{BLOCK_START}\nDROP TABLE {token};\n{BLOCK_END}\nb {token}"
    assert strip_retirement_block(text) == f"a\nb {token}"
