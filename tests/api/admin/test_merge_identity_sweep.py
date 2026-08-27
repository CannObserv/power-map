"""AST ratchets for the two rules #467 established, with no allowlist.

The org-merge regression was not a typo — it was one of two near-identical blocks
drifting from the primitive its siblings used, in a file nobody re-reads. Both
rules below are cheap to state and impossible to notice by eye across five merge
paths, so they are enforced structurally instead:

1. **Identity ratchet.** No merge path may `INSERT INTO role_assignments`.
   Migrating a tenure is a re-point (`UPDATE ... SET role_id` / `SET person_id`);
   an INSERT there is by definition a reminted ULID, which silently breaks every
   `pm_assignment_id` anchor a producer holds.
2. **Tombstone ratchet.** Any admin module that hard-deletes a `role` or a
   `role_assignment` must also emit a tombstone, because the outbox triggers fire
   on INSERT/UPDATE only — a DELETE is invisible to `/api/v1/changes` unless a
   `deleted_entities` row announces it.
"""

import ast
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "admin"

#: Every path that folds one entity into another. Named explicitly: a new merge
#: module is a deliberate addition and should be added here consciously.
MERGE_MODULES = ("orgs_merge.py", "people_merge.py", "orgs_roles.py")

DELETE_LITERALS = ("DELETE FROM roles", "DELETE FROM role_assignments")
TOMBSTONE_MARKERS = ("record_merge_tombstones", "deleted_entities")


def _string_constants(path: Path) -> list[str]:
    """Every string literal in the module, including implicitly concatenated SQL."""
    tree = ast.parse(path.read_text())
    return [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_merge_modules_exist():
    """Guards the ratchets below against silently passing on a renamed file."""
    for name in MERGE_MODULES:
        assert (ADMIN_DIR / name).is_file(), f"{name} moved — update MERGE_MODULES"


def test_no_merge_path_inserts_a_role_assignment():
    """#467: migrating a tenure re-points it; an INSERT remints its ULID."""
    offenders = [
        name
        for name in MERGE_MODULES
        if any("INSERT INTO role_assignments" in s for s in _string_constants(ADMIN_DIR / name))
    ]
    assert not offenders, (
        f"{offenders} INSERTs a role_assignment during a merge. Re-point the existing"
        " row instead — a new id breaks every pm_assignment_id anchor (#467)."
    )


def test_every_role_or_assignment_hard_delete_emits_a_tombstone():
    """#467: a DELETE fires no outbox trigger, so it must be announced explicitly."""
    offenders = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        literals = _string_constants(path)
        deletes = any(any(lit in s for lit in DELETE_LITERALS) for s in literals)
        if not deletes:
            continue
        source = path.read_text()
        if not any(marker in source for marker in TOMBSTONE_MARKERS):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} hard-delete a role or role_assignment without a tombstone."
        " The change-feed triggers are INSERT/UPDATE-only, so subscribers see"
        " nothing unless a deleted_entities row announces it (#467)."
    )
