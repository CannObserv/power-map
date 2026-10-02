"""AST ratchet (#605): every admin hard delete drops its entity's polymorphic rows.

``identifiers``, ``links``, ``contact_methods`` and the rest key on an entity id
with no FK, so a bare ``DELETE FROM people`` strands them. The merge paths
re-home instead (``test_merge_identity_sweep.py`` holds those); every other
admin module that hard-deletes an entity must call ``delete_entity_ancillary``.
The end-to-end proof per route is ``test_hard_delete_ancillary.py``.
"""

import ast
import re
from pathlib import Path

from tests.api.admin.test_merge_identity_sweep import MERGE_MODULES, SRC_DIR

ADMIN_DIR = SRC_DIR / "api" / "admin"
MERGE_PATHS = {SRC_DIR / name for name in MERGE_MODULES}
ENTITY_DELETE = re.compile(
    r"DELETE FROM (people|organizations|jurisdictions|roles|role_assignments)\b"
)


def _deletes_an_entity(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and ENTITY_DELETE.search(n.value)
        for n in ast.walk(tree)
    )


def _calls(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
        for n in ast.walk(tree)
    )


def test_sweep_sees_the_hard_delete_routes():
    """Guards the ratchet below against passing vacuously on a renamed file."""
    found = {
        p.name for p in ADMIN_DIR.glob("*.py") if p not in MERGE_PATHS and _deletes_an_entity(p)
    }
    assert {
        "people.py",
        "orgs.py",
        "jurisdictions.py",
        "roles.py",
        "role_assignments.py",
    } <= found


def test_every_admin_hard_delete_drops_its_polymorphic_rows():
    offenders = [
        p.name
        for p in sorted(ADMIN_DIR.glob("*.py"))
        if p not in MERGE_PATHS
        and _deletes_an_entity(p)
        and not _calls(p, "delete_entity_ancillary")
    ]
    assert not offenders, (
        f"{offenders} hard-delete an entity without delete_entity_ancillary. Its"
        " identifiers / links / contact_methods have no FK and would dangle (#605)."
    )
