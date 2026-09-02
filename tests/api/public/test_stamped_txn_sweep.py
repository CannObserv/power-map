"""Sweep: public write transactions must use ``stamped_transaction`` (#491).

Outbox attribution rides a txn-local GUC set at transaction open. Which writes
reach the outbox is non-obvious by design — ancillary touch triggers (#327)
fan out to parent entities — so rather than enumerate reachable paths, every
transaction opened in ``src/api/public`` goes through the helper. A bare
``db.transaction()`` in a route module would produce unattributed feed rows
that a consumer reads as curator edits (and read-backs churn returns, #491).

Hermetic AST guard; behaviour lives in ``test_changes_source_key.py``.
"""

import ast
import pathlib

_PUBLIC_API_DIR = pathlib.Path(__file__).resolve().parents[3] / "src" / "api" / "public"

# The helper itself owns the one legitimate bare transaction() call.
_EXEMPT = {"deps.py"}


def test_no_bare_transaction_in_public_routes():
    offenders = []
    for path in sorted(_PUBLIC_API_DIR.glob("*.py")):
        if path.name in _EXEMPT:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "transaction"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "bare .transaction() in public route modules — use "
        f"stamped_transaction(db, key_id) from src.api.public.deps: {offenders}"
    )
