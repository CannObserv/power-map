"""Source-level sweep: every admin write to a producer-owned slot is tracked (#498).

The pin rule (docs/ADMIN_OVERLAY.md): a write that changes a slot's value pins
the value after the edit, which each edit site does by wrapping its transaction
in ``tracked()``. A new route that writes a slot table without it pins nothing,
and the curator's correction is reverted by the next apply without a word — the
one outcome the overlay exists to rule out. So this parses every
``src/api/admin/*.py`` module and fails:

- a mutation handler whose own body, or a same-module function it calls
  (transitively), writes a slot table, and which never calls ``tracked(``;
- a function that writes a slot table and no mutation handler in its module
  reaches — it could be imported and called from anywhere the first rule
  cannot see.

A slot table is one a slot in ``overlay_slots.SLOTS`` reads: ``person_names``
and ``organization_names`` (``{names_table}`` in the names factory),
``organization_acronyms``, ``entity_events`` and ``organizations.parent_id``.
Heuristic, like the fallback sweep: SQL is matched in string literals, so it
cannot see a write built out of pieces. Vetted exceptions carry a reason, and
an entry that no longer names a slot-writing function fails too.
"""

import ast
import re
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "admin"

_MUTATION_METHODS = {"post", "put", "delete", "patch"}
_SLOT_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
    r"(?:(?:person_names|organization_names|organization_acronyms|entity_events)\b"
    r"|\{names_table\})"
    r"|\bUPDATE\s+organizations\s+SET\b[^;]*?\bparent_id\s*=",
    re.IGNORECASE | re.DOTALL,
)

# "<file>.py::<handler>" routes that write a slot table without pinning. Each needs a reason.
ALLOWED_ROUTES: dict[str, str] = {
    "orgs.py::org_create": "a new org has no producer_crosswalk row: outside every row scope",
    "people.py::person_create": "a new person has no producer_crosswalk row: outside every scope",
    "orgs.py::org_delete": "hard-deletes an archived org; no slot is left to read",
    "people.py::person_delete": "hard-deletes an archived person; no slot is left to read",
    "_events_shared.py::event_delete": "hard-deletes an archived event; the dissolved slot reads"
    " unarchived events only",
    "orgs_merge.py::org_merge": "a merge carries pins by rehome_curation_overlay (#514)",
    "orgs_merge.py::org_merge_with": "a merge carries pins by rehome_curation_overlay (#514)",
    "orgs_succession.py::link_successor": "writes a succeeded_by event; the dissolved slot reads"
    " dissolved events only",
}
# "<file>.py::<function>" slot writers no same-module route reaches. Each needs a reason.
ALLOWED_HELPERS: dict[str, str] = {
    "orgs_names.py::_maybe_promote_sole_name": "handed to make_names_router; it runs inside"
    " name_delete's tracked() block",
}

# The edit sites #498 tracks — the sweep must see each as a slot writer, or its
# matcher has gone blind and every assertion below proves nothing.
TRACKED_SITES = {
    "_names_shared.py::name_create",
    "_names_shared.py::name_edit_row_post",
    "_names_shared.py::name_delete",
    "orgs_acronyms.py::acronym_create",
    "orgs_acronyms.py::acronym_edit_row_post",
    "orgs_acronyms.py::acronym_delete",
    "orgs.py::org_inline_parent_post",
    "orgs.py::children_add",
    "orgs.py::children_remove",
    "_events_shared.py::event_create",
    "_events_shared.py::event_edit_row_post",
    "_events_shared.py::event_archive",
    "_events_shared.py::event_unarchive",
}

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own_nodes(fn: ast.AST):
    """The nodes of ``fn``'s body, not descending into functions nested in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _FUNCS):
            stack.extend(ast.iter_child_nodes(node))


def _writes_slot(fn: ast.AST) -> bool:
    for node in _own_nodes(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = ast.unparse(node)  # keeps `{names_table}` visible
        else:
            continue
        if _SLOT_WRITE.search(text):
            return True
    return False


def _calls(fn: ast.AST) -> set[str]:
    return {
        n.func.id
        for n in _own_nodes(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _is_mutation(fn: ast.AST) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _MUTATION_METHODS
        for dec in fn.decorator_list
    )


def _survey() -> tuple[dict[str, bool], set[str]]:
    """``({route: calls tracked()}, unreached helpers)`` over every slot-writing function."""
    routes: dict[str, bool] = {}
    helpers: set[str] = set()
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, _FUNCS)}
        reached: set[str] = set()
        for fn in funcs.values():
            if not _is_mutation(fn):
                continue
            seen, todo = set(), [fn.name]
            while todo:
                name = todo.pop()
                if name in seen or name not in funcs:
                    continue
                seen.add(name)
                todo.extend(_calls(funcs[name]))
            reached |= seen
            if any(_writes_slot(funcs[n]) for n in seen):
                routes[f"{path.name}::{fn.name}"] = "tracked" in _calls(fn)
        for fn in funcs.values():
            if fn.name not in reached and not _is_mutation(fn) and _writes_slot(fn):
                helpers.add(f"{path.name}::{fn.name}")
    return routes, helpers


def test_every_route_that_writes_a_slot_table_is_tracked():
    routes, _ = _survey()
    offenders = sorted(r for r, is_tracked in routes.items() if not is_tracked)
    offenders = [r for r in offenders if r not in ALLOWED_ROUTES]
    assert offenders == [], (
        "Admin routes writing a producer-owned slot table without tracked() — the edit"
        " would pin nothing and the next apply would revert it (docs/ADMIN_OVERLAY.md):"
        f" {offenders}. Wrap the write in tracked(...), or allowlist with a reason."
    )


def test_every_slot_writer_is_reached_from_a_route_the_sweep_checks():
    _, helpers = _survey()
    offenders = sorted(h for h in helpers if h not in ALLOWED_HELPERS)
    assert offenders == [], (
        "Functions writing a slot table that no route in their module reaches, so the"
        f" tracked() check cannot see their callers: {offenders}. Call them from a"
        " tracked route in the same module, or allowlist with a reason."
    )


def test_the_sweep_sees_every_tracked_edit_site():
    """Self-check: the matcher still finds each site #498 tracks, as tracked."""
    routes, _ = _survey()
    assert {r for r in TRACKED_SITES if routes.get(r)} == TRACKED_SITES


def test_every_allowlist_entry_still_names_a_slot_writer():
    """An entry whose function stopped writing a slot table, or was renamed, is dead."""
    routes, helpers = _survey()
    assert sorted(set(ALLOWED_ROUTES) - set(routes)) == []
    assert sorted(set(ALLOWED_HELPERS) - helpers) == []
