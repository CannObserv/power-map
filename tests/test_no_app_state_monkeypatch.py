"""No test monkeypatches an attribute on a Starlette ``State`` (#736).

From pytest 9.2, ``monkeypatch.setattr`` takes the value to restore from the
target's instance ``__dict__`` (pytest#14969). Starlette's ``State`` keeps its
attributes in ``_state`` behind ``__getattr__``, so the old value reads as unset
and the undo *deletes* the attribute: ``app.state.embedding_registry`` vanished
for the rest of the session, and the failure surfaced in whichever later test
read it (pytest#15099, open upstream).

Swap what a route reads through ``app.dependency_overrides`` instead
(``monkeypatch.setitem(app.dependency_overrides, getter, ...)``), whose undo
restores the exact entry. The sweep flags any ``monkeypatch.setattr`` /
``monkeypatch.delattr`` whose target is an attribute named ``state``
(``app.state``, ``request.app.state``, ...).
"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _state_patches(source: str) -> list[int]:
    """Line numbers of ``monkeypatch.setattr/delattr(<...>.state, ...)`` calls."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("setattr", "delattr")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "monkeypatch"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "state"
        ):
            hits.append(node.lineno)
    return hits


def test_the_detector_is_live() -> None:
    assert _state_patches('monkeypatch.setattr(app.state, "registry", x)') == [1]
    assert _state_patches('monkeypatch.delattr(request.app.state, "registry")') == [1]
    assert _state_patches("monkeypatch.setitem(app.dependency_overrides, getter, f)") == []


def test_no_test_monkeypatches_a_starlette_state() -> None:
    offenders = [
        f"{path.relative_to(TESTS_DIR.parent)}:{line}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        for line in _state_patches(path.read_text())
    ]
    assert not offenders, (
        "monkeypatch on a Starlette State deletes the attribute at undo from pytest 9.2 "
        "(pytest#15099); override the route's getter through app.dependency_overrides "
        f"instead: {', '.join(offenders)}"
    )
