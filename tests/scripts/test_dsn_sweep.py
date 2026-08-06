"""AST sweep: every operational script resolves its target through `_dsn.py` (#399).

Three assertions, each guarding a class of mistake that a convention alone did
not prevent:

1. A script that opens a connection goes through `scripts/_dsn.py`, so its
   target is echoed and labelled.
2. Nobody reads `DATABASE_URL` from the environment directly — that is the
   pattern that made "which database am I about to write to?" invisible.
3. A script containing write SQL declares `--execute`. This is the assertion
   #402 exists because of: `--execute` was believed universal, two scripts did
   not have it, and nothing checked.

Deliberately **no allowlist.** An exemption list is a place for a live script
to hide, and the executed-once migrations cost one mechanical edit each to
bring into line. If a new script genuinely cannot comply, the fix is to change
this test with a reason in the diff — not to add a name to a set.
"""

import ast
import re
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
DSN_MODULE = "_dsn"

# Entry points into scripts/_dsn.py that echo a target. A multi-DSN script
# (audit_schema_constraint_parity) calls echo_target per connection rather than
# resolve_dsn once, so either satisfies assertion 1.
DSN_FUNCTIONS = frozenset({"resolve_dsn", "echo_target"})

CONNECT_FUNCTIONS = frozenset({"connect", "create_pool"})

WRITE_SQL = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b", re.IGNORECASE)


def script_paths() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.py") if p.name != "__init__.py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _docstrings(tree: ast.Module) -> set[int]:
    """Line numbers of docstring nodes — excluded when scanning for SQL.

    A module docstring that says "connect to DATABASE_URL and DELETE FROM x"
    is documentation, not a write.
    """
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                lines.add(node.body[0].lineno)
    return lines


def opens_a_connection(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in CONNECT_FUNCTIONS
        for node in ast.walk(tree)
    )


def uses_dsn_module(tree: ast.Module) -> bool:
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith(DSN_MODULE)
        for alias in node.names
    }
    return bool(imported & DSN_FUNCTIONS)


def reads_database_url_directly(tree: ast.Module) -> bool:
    """`os.environ.get("DATABASE_URL")` or `os.environ["DATABASE_URL"]`."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and any(
                isinstance(a, ast.Constant) and a.value == "DATABASE_URL" for a in node.args[:1]
            )
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "DATABASE_URL"
        ):
            return True
    return False


def contains_write_sql(tree: ast.Module) -> bool:
    doc_lines = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.lineno not in doc_lines
        and WRITE_SQL.search(node.value)
        for node in ast.walk(tree)
    )


def declares_execute_flag(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and any(isinstance(a, ast.Constant) and a.value == "--execute" for a in node.args)
        for node in ast.walk(tree)
    )


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


def test_the_sweep_actually_found_scripts():
    """A glob that silently matched nothing would make every test below vacuous."""
    assert len(script_paths()) > 30


@pytest.mark.parametrize("path", script_paths(), ids=lambda p: p.name)
def test_connecting_script_resolves_through_dsn_module(path):
    tree = _tree(path)
    if not opens_a_connection(tree):
        pytest.skip("does not open a connection")
    assert uses_dsn_module(tree), (
        f"{path.name} opens a connection without importing resolve_dsn/echo_target from "
        "scripts/_dsn.py — its target would never be echoed. See docs/CONVENTIONS.md "
        '§"Operational scripts — dry run by default & target echo".'
    )


@pytest.mark.parametrize("path", script_paths(), ids=lambda p: p.name)
def test_no_direct_database_url_reads(path):
    if path.name == f"{DSN_MODULE}.py":
        pytest.skip("the resolver is the one place that reads the environment")
    assert not reads_database_url_directly(_tree(path)), (
        f"{path.name} reads DATABASE_URL from the environment directly. Use "
        "add_dsn_args(parser) + resolve_dsn(args, parser) so the target is labelled and echoed."
    )


@pytest.mark.parametrize("path", script_paths(), ids=lambda p: p.name)
def test_write_sql_requires_an_execute_flag(path):
    tree = _tree(path)
    if not contains_write_sql(tree):
        pytest.skip("no write SQL")
    assert declares_execute_flag(tree), (
        f"{path.name} contains write SQL but declares no --execute flag, so a bare invocation "
        "writes to production. This is the #402 defect; gate the write."
    )
