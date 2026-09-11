"""AST ratchets for the rules #467 and #514 established, with no allowlist.

The org-merge regression was not a typo — it was one of two near-identical blocks
drifting from the primitive its siblings used, in a file nobody re-reads. Both
rules below are cheap to state and impossible to notice by eye across five merge
paths, so they are enforced structurally instead:

1. **Identity ratchet.** No merge path may `INSERT INTO role_assignments`.
   Migrating a tenure is a re-point (`UPDATE ... SET role_id` / `SET person_id`);
   an INSERT there is by definition a reminted ULID, which silently breaks every
   `pm_assignment_id` anchor a producer holds.
2. **Tombstone ratchet.** Any admin module — or merge module outside the admin
   tree, like the core person merge — that hard-deletes a `role` or a
   `role_assignment` must also emit a tombstone, because the outbox triggers fire
   on INSERT/UPDATE only — a DELETE is invisible to `/api/v1/changes` unless a
   `deleted_entities` row announces it.
3. **Overlay ratchet (#514).** Wherever a merge path mirrors a subscription onto
   a survivor, it re-homes the loser's `curation_overlay` rows too. Both tables
   are keyed on an id with no FK, and an override left on a merged-away id stops
   applying without a word — the mapping layer joins it on the surviving pm_id.
"""

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[3] / "src"
ADMIN_DIR = SRC_DIR / "api" / "admin"

#: Every path that folds one entity into another, relative to `src/`. Named
#: explicitly: a new merge module is a deliberate addition and should be added
#: here consciously. The person merge primitive moved to core for the applier
#: (#514); its admin module stays listed because it still hosts the merge routes.
MERGE_MODULES = (
    "api/admin/orgs_merge.py",
    "api/admin/people_merge.py",
    "api/admin/orgs_roles.py",
    "core/person_merge.py",
)

#: Modules the tombstone ratchet reads: every admin module, plus the merge
#: modules that live outside it — a primitive moved out of the admin tree must
#: not move out of the ratchet with it.
TOMBSTONE_SCOPE = sorted({*ADMIN_DIR.glob("*.py"), *(SRC_DIR / name for name in MERGE_MODULES)})

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
        assert (SRC_DIR / name).is_file(), f"{name} moved — update MERGE_MODULES"


def test_no_merge_path_inserts_a_role_assignment():
    """#467: migrating a tenure re-points it; an INSERT remints its ULID."""
    offenders = [
        name
        for name in MERGE_MODULES
        if any("INSERT INTO role_assignments" in s for s in _string_constants(SRC_DIR / name))
    ]
    assert not offenders, (
        f"{offenders} INSERTs a role_assignment during a merge. Re-point the existing"
        " row instead — a new id breaks every pm_assignment_id anchor (#467)."
    )


def test_every_role_or_assignment_hard_delete_emits_a_tombstone():
    """#467: a DELETE fires no outbox trigger, so it must be announced explicitly."""
    offenders = []
    for path in TOMBSTONE_SCOPE:
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


def _calls(path: Path, name: str) -> int:
    """How many times the module calls ``name`` (bare or attribute form)."""
    tree = ast.parse(path.read_text())
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
    )


def test_every_subscription_mirror_carries_the_curation_overlay():
    """#514: a merge step that re-homes watchers re-homes curator overrides with them."""
    offenders = {
        name: (mirrors, rehomes)
        for name in MERGE_MODULES
        if (mirrors := _calls(SRC_DIR / name, "mirror_subscriptions"))
        != (rehomes := _calls(SRC_DIR / name, "rehome_curation_overlay"))
    }
    assert not offenders, (
        f"{offenders} (mirror_subscriptions calls, rehome_curation_overlay calls) differ."
        " Each merge step that mirrors a subscription must re-home the loser's"
        " curation_overlay rows beside it (#514)."
    )
