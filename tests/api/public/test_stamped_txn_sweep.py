"""Sweep: public write transactions must use ``stamped_transaction`` (#491).

Outbox attribution rides a txn-local GUC set at transaction open. Which writes
reach the outbox is non-obvious by design — ancillary touch triggers (#327)
fan out to parent entities — so rather than enumerate reachable paths, every
transaction opened in ``src/api/public`` goes through the helper. A bare
``db.transaction()`` in a route module would produce unattributed feed rows
that a consumer reads as curator edits (and read-backs churn returns, #491).

The helper's own file is not exempt: ``deps.py`` must hold exactly one bare
``.transaction()`` call, inside ``stamped_transaction`` itself — a blanket
exemption would leave the guard blind in the one file where auth helpers
accrete (CR round 1).

Hermetic AST guard; behaviour lives in ``test_changes_source_key.py``.
"""

import ast
import pathlib

_PUBLIC_API_DIR = pathlib.Path(__file__).resolve().parents[3] / "src" / "api" / "public"
_HELPER_FILE = "deps.py"
_HELPER_NAME = "stamped_transaction"


def _bare_transaction_calls(tree: ast.Module) -> list[tuple[int, str | None]]:
    """(lineno, enclosing function name) for every ``<x>.transaction()`` call."""
    found: list[tuple[int, str | None]] = []

    def visit(node: ast.AST, owner: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "transaction"
        ):
            found.append((node.lineno, owner))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, None)
    return found


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def test_no_bare_transaction_in_public_routes():
    offenders = []
    for path in sorted(_PUBLIC_API_DIR.glob("*.py")):
        if path.name == _HELPER_FILE:
            continue
        for lineno, _ in _bare_transaction_calls(_parse(path)):
            offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "bare .transaction() in public route modules — use "
        f"stamped_transaction(db, key_id) from src.api.public.deps: {offenders}"
    )


def test_helper_file_holds_exactly_one_bare_transaction_inside_the_helper():
    calls = _bare_transaction_calls(_parse(_PUBLIC_API_DIR / _HELPER_FILE))
    assert [owner for _, owner in calls] == [_HELPER_NAME], (
        f"{_HELPER_FILE} must contain exactly one bare .transaction() call, inside "
        f"{_HELPER_NAME}(); found {[f'line {ln} in {o}' for ln, o in calls]}"
    )
