"""``power-map-client`` declares exactly what it imports (#618).

A consumer installs the client's ``[project].dependencies``. A missing one
breaks the import in their environment, not in ours, where the dev group
happens to provide it. An extra one is forced on every consumer for nothing.
The generator's needs can change with its version, so the list is checked
against the code rather than maintained by hand.
"""

import ast
import importlib.metadata
import re
import sys
import tomllib

from scripts.regenerate_client import SDK_DIR

PACKAGE = SDK_DIR / "src" / "power_map_client"


def _third_party_imports() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found - set(sys.stdlib_module_names) - {"power_map_client", "__future__"}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _distributions(imports: set[str]) -> set[str]:
    """The distributions that provide ``imports``: an import name is not a
    distribution name (``dateutil`` comes from ``python-dateutil``)."""
    providers = importlib.metadata.packages_distributions()
    return {_normalize(dist) for name in imports for dist in providers.get(name, [name])}


def _declared() -> set[str]:
    deps = tomllib.loads((SDK_DIR / "pyproject.toml").read_text())["project"]["dependencies"]
    return {_normalize(re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0]) for d in deps}


def test_declared_dependencies_are_exactly_the_imports():
    assert _declared() == _distributions(_third_party_imports())


def test_the_scan_sees_the_generated_tree():
    """Guards the scan itself: an empty walk would make the equality vacuous."""
    assert {"attrs", "httpx"} <= _third_party_imports()


def test_an_import_is_matched_to_its_distribution_name():
    """``dateutil`` is imported, ``python-dateutil`` is declared: one dependency."""
    assert _distributions({"dateutil", "httpx"}) == {"python_dateutil", "httpx"}
