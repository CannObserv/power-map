"""AST sweep: every operational script resolves its target through `_dsn.py` (#399).

Three assertions, each guarding a class of mistake that a convention alone did
not prevent:

1. A script that opens a connection goes through `scripts/_dsn.py`, so its
   target is echoed and labelled.
2. Nobody reads `DATABASE_URL`, `TEST_DATABASE_URL` or `MIGRATIONS_DATABASE_URL`
   from the environment directly — reading the first is the pattern that made
   "which database am I about to write to?" invisible, and reading the second
   bypasses the `--test` guard against falling through to production.
3. A script containing write SQL declares `--execute`. This is the assertion
   #402 exists because of: `--execute` was believed universal, two scripts did
   not have it, and nothing checked.

Deliberately **no allowlist.** An exemption list is a place for a live script
to hide, and the executed-once migrations cost one mechanical edit each to
bring into line. If a new script genuinely cannot comply, the fix is to change
this test with a reason in the diff — not to add a name to a set. (The one
exception, `REFERENCE_DSN_EXEMPTION`, names a variable in a file rather than a
file, and carries its reasoning inline.)

A second section at the bottom holds **detector self-tests**. The sweep passing
means "no script violates the rules" only if the detectors actually fire — an
always-false detector looks identical from the outside — so each is shown a
synthetic violation it must catch and a compliant sample it must not flag.
Extend those alongside any change to the detectors above.
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

DSN_ENV_VARS = frozenset({"DATABASE_URL", "TEST_DATABASE_URL", "MIGRATIONS_DATABASE_URL"})

# audit_schema_constraint_parity compares two databases, so its --reference-url
# default chain (PARITY_REFERENCE_URL, then TEST_DATABASE_URL) is a second real
# target rather than a bypass of the first. Its --target-url goes through
# default_dsn() and both connections are echoed with a role, so the guarantee
# this sweep protects still holds. Narrow on purpose: the variable is named, not
# just the script.
REFERENCE_DSN_EXEMPTION = {"audit_schema_constraint_parity.py": {"TEST_DATABASE_URL"}}

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
    """Imports *and* calls one of the `_dsn` entry points.

    Both halves are checked: an import alone would satisfy this while the script
    still connected to an unannounced target. ruff's F401 would also catch the
    unused import, but this test should not depend on another tool to mean what
    it says.
    """
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith(DSN_MODULE)
        for alias in node.names
    } & DSN_FUNCTIONS
    if not imported:
        return False
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return bool(imported & called)


def dsn_env_vars_read(tree: ast.Module) -> set[str]:
    """DSN environment variables read directly: `os.environ.get(X)` / `os.environ[X]`.

    All three, not just DATABASE_URL: a script reading TEST_DATABASE_URL itself
    bypasses the `--test` guard (which exists precisely so an unset variable
    cannot fall through to production), and one reading MIGRATIONS_DATABASE_URL
    bypasses the label.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in DSN_ENV_VARS
        ):
            found.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in DSN_ENV_VARS
        ):
            found.add(node.slice.value)
    return found


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
def test_no_direct_dsn_env_reads(path):
    if path.name == f"{DSN_MODULE}.py":
        pytest.skip("the resolver is the one place that reads the environment")
    allowed = REFERENCE_DSN_EXEMPTION.get(path.name, set())
    offending = dsn_env_vars_read(_tree(path)) - allowed
    assert not offending, (
        f"{path.name} reads {', '.join(sorted(offending))} from the environment directly. Use "
        "add_dsn_args(parser) + resolve_dsn(args, parser) so the target is labelled and echoed, "
        "and so --test cannot fall through to production."
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


# --------------------------------------------------------------------------- #
# Detector self-tests
# --------------------------------------------------------------------------- #
#
# The sweep above only passes because no script violates it. That is also what
# a broken detector looks like, so each one is shown a synthetic violation it
# must catch and a compliant sample it must not flag.


# Parsed by the detectors, never executed — `conn` is deliberately undefined
# and the connect result discarded; only the AST shape matters here.
COMPLIANT = """
import argparse
import asyncpg
from scripts._dsn import add_dsn_args, resolve_dsn

def main():
    parser = argparse.ArgumentParser()
    add_dsn_args(parser)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncpg.connect(dsn)
    conn.execute("INSERT INTO t (a) VALUES ($1)")
"""


def test_detector_flags_a_direct_database_url_read():
    assert dsn_env_vars_read(ast.parse('import os\nx = os.environ.get("DATABASE_URL")')) == {
        "DATABASE_URL"
    }


def test_detector_flags_a_direct_test_database_url_read():
    """The #399 addition: reading this directly sidesteps the --test guard."""
    assert dsn_env_vars_read(ast.parse('import os\nx = os.environ["TEST_DATABASE_URL"]')) == {
        "TEST_DATABASE_URL"
    }


def test_detector_flags_a_direct_migrations_dsn_read():
    assert dsn_env_vars_read(
        ast.parse('import os\nx = os.environ.get("MIGRATIONS_DATABASE_URL")')
    ) == {"MIGRATIONS_DATABASE_URL"}


def test_detector_ignores_unrelated_env_reads():
    assert (
        dsn_env_vars_read(ast.parse('import os\nx = os.environ.get("PARITY_REFERENCE_URL")'))
        == set()
    )


def test_compliant_sample_reads_no_dsn_env_vars():
    assert dsn_env_vars_read(ast.parse(COMPLIANT)) == set()


def test_import_without_a_call_does_not_satisfy_the_dsn_check():
    """An unused import would otherwise pass assertion 1 while the script
    connected to a target it never announced."""
    source = "from scripts._dsn import resolve_dsn\nimport asyncpg\nasyncpg.connect('x')"
    tree = ast.parse(source)
    assert opens_a_connection(tree)
    assert not uses_dsn_module(tree)


def test_import_with_a_call_satisfies_the_dsn_check():
    assert uses_dsn_module(ast.parse(COMPLIANT))


def test_connection_detector_finds_create_pool():
    assert opens_a_connection(ast.parse("import asyncpg\nasyncpg.create_pool('x')"))


def test_write_sql_detector_ignores_docstrings():
    """A module docstring mentioning DELETE FROM is documentation, not a write."""
    assert not contains_write_sql(ast.parse('"""Explains DELETE FROM rows."""\nx = 1'))


def test_write_sql_detector_finds_real_sql():
    assert contains_write_sql(ast.parse(COMPLIANT))


def test_execute_flag_detector():
    assert declares_execute_flag(ast.parse(COMPLIANT))
    assert not declares_execute_flag(ast.parse('p.add_argument("--dry-run")'))
