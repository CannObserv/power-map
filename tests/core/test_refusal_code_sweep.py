"""#882: no observation refusal leaves ``src/`` without a stable code.

``reason_code`` is the contract a producer acts on, and it covers every refusal.
A refusal written the old way, as a bare string, would reach the wire with no
code, and no test of that one path would notice. This sweep parses every Python
file under ``src/`` and fails on:

- an ``ObservationRejected(...)`` whose code is not an ``ObservationReasonCode``
  member (``ObservationRejected.of(refusal)`` is the other door, and takes one);
- a rejected ``ObservationResponse(...)`` with no ``reason_code=``;
- a resolver's ``Disposition.REJECTED`` answer whose reason is a bare string
  rather than a ``Refusal`` carrying its code.
"""

import ast
from pathlib import Path

_CODE_FORMS = {"ObservationReasonCode", "name_reason_code"}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value)
    if isinstance(node, ast.Call):
        return _name(node.func)
    return None


def _is_code(node: ast.AST) -> bool:
    """``ObservationReasonCode.X``, ``ObservationReasonCode(v)`` or ``name_reason_code(r)``."""
    return _name(node) in _CODE_FORMS


def _is_rejected_marker(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "REJECTED"
        and isinstance(node.value, ast.Name)
        and node.value.id == "Disposition"
    )


def _is_refusal(node: ast.AST) -> bool:
    """A ``Refusal(...)`` built here, or a name the resolver typed ``Refusal | None``."""
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == "Refusal"
    return isinstance(node, ast.Name)


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    out = []
    for node in ast.walk(tree):
        where = f"{path}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "ObservationRejected" and (
                not node.args or not _is_code(node.args[0])
            ):
                out.append(f"{where} ObservationRejected without an ObservationReasonCode")
            if node.func.id == "ObservationResponse":
                kw = {k.arg: k.value for k in node.keywords}
                disp = kw.get("disposition")
                if (
                    isinstance(disp, ast.Constant)
                    and disp.value == "rejected"
                    and "reason_code" not in kw
                ):
                    out.append(f"{where} rejected ObservationResponse without reason_code")
            if node.func.id == "AssignmentResolution" and any(
                _is_rejected_marker(a) for a in node.args
            ):
                if len(node.args) < 3 or not _is_refusal(node.args[2]):
                    out.append(f"{where} REJECTED AssignmentResolution without a Refusal")
        if isinstance(node, ast.Tuple) and any(_is_rejected_marker(e) for e in node.elts):
            if not _is_refusal(node.elts[-1]):
                out.append(f"{where} REJECTED answer without a Refusal")
    return out


def _sweep(paths) -> list[str]:
    return [v for p in paths for v in _violations(p)]


def test_every_refusal_under_src_carries_a_code():
    assert _sweep(sorted(Path("src").rglob("*.py"))) == []


def test_the_sweep_sees_the_refusal_sites():
    """The sweep is not vacuous: src holds raises and REJECTED answers it checked."""
    text = Path("src/core/observation.py").read_text()
    assert "ObservationRejected(ObservationReasonCode." in text
    assert "Disposition.REJECTED, Refusal(" in text


def test_sweep_catches_a_bare_string_raise(tmp_path):
    stray = tmp_path / "stray.py"
    stray.write_text('raise ObservationRejected("parent_cycle")\n')
    assert len(_sweep([stray])) == 1


def test_sweep_catches_a_rejected_response_without_a_code(tmp_path):
    stray = tmp_path / "stray.py"
    stray.write_text('ObservationResponse(disposition="rejected", reason="x")\n')
    assert len(_sweep([stray])) == 1


def test_sweep_catches_a_bare_string_resolver_reason(tmp_path):
    stray = tmp_path / "stray.py"
    stray.write_text(
        'def f():\n    return "", Disposition.REJECTED, "title_required"\n'
        'def g():\n    return AssignmentResolution("", Disposition.REJECTED, "x")\n'
    )
    assert len(_sweep([stray])) == 2


def test_sweep_passes_the_coded_forms(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text(
        "raise ObservationRejected(ObservationReasonCode.PARENT_CYCLE)\n"
        "raise ObservationRejected(ObservationReasonCode(result.reason))\n"
        "raise ObservationRejected.of(reason)\n"
        'ObservationResponse(disposition="rejected", reason="x", reason_code=c)\n'
        "def f():\n    return '', Disposition.REJECTED, Refusal(ObservationReasonCode.X)\n"
    )
    assert _sweep([ok]) == []
