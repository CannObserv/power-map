"""An HTMX refusal never swaps into the mutation's target (#684).

htmx 2's default ``responseHandling`` swaps every 2xx body, an empty one
included. A refused mutation answered with an empty 200 + warning flash
therefore blanks whatever the control targets: a whole ``<tbody>`` for the
names/acronyms tables, the row itself for a settings-type delete. Only an
``HX-Reswap`` / ``HX-Retarget`` response header changes that.

The rule (docs/ADMIN.md § Refusals): an HTMX refusal returns
``htmx_refusal(body)`` (``HX-Reswap: none``) or retargets its body to the form
row. This sweep flags any ``HTMLResponse``/``Response`` under ``src/api`` that
is an empty-bodied 2xx carrying a ``warning``/``error`` flash with neither
header. Vetted exceptions go in ALLOWED with a reason comment.
"""

import ast
import json
from pathlib import Path

import pytest

from src.api.admin.deps import htmx_refusal

API_DIR = Path(__file__).resolve().parents[3] / "src" / "api"

_RESPONSE_CLASSES = {"HTMLResponse", "Response"}
_REFUSAL_LEVELS = {"warning", "error"}
_SWAP_OVERRIDES = ("HX-Reswap", "HX-Retarget")

# "<relpath>::L<line>" sites exempt from the sweep. Each needs a reason.
ALLOWED: frozenset[str] = frozenset()


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _kw(node: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in node.keywords if k.arg == name), None)


def _is_empty_body(node: ast.Call) -> bool:
    content = node.args[0] if node.args else _kw(node, "content")
    if content is None:
        return True
    return isinstance(content, ast.Constant) and content.value in ("", b"", None)


def _swaps_on_success(node: ast.Call) -> bool:
    """True unless the status is a constant htmx will not swap (204, or non-2xx)."""
    status = _kw(node, "status_code")
    if status is None and len(node.args) >= 2:
        status = node.args[1]
    if status is None:
        return True
    if isinstance(status, ast.Constant) and isinstance(status.value, int):
        return 200 <= status.value < 300 and status.value != 204
    return True


def _has_refusal_flash(headers: ast.expr) -> bool:
    for sub in ast.walk(headers):
        if (
            isinstance(sub, ast.Call)
            and _call_name(sub) == "flash_trigger"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
            and sub.args[0].value in _REFUSAL_LEVELS
        ):
            return True
    return False


def unguarded_refusals(source: str, label: str) -> list[str]:
    """``<label>::L<line>`` for each empty 2xx refusal that would swap."""
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and _call_name(node) in _RESPONSE_CLASSES):
            continue
        headers = _kw(node, "headers")
        if headers is None or not _has_refusal_flash(headers):
            continue
        if not (_is_empty_body(node) and _swaps_on_success(node)):
            continue
        if any(marker in ast.unparse(headers) for marker in _SWAP_OVERRIDES):
            continue
        offenders.append(f"{label}::L{node.lineno}")
    return offenders


def test_no_htmx_refusal_swaps_an_empty_body():
    offenders = []
    for path in sorted(API_DIR.rglob("*.py")):
        rel = path.relative_to(API_DIR).as_posix()
        offenders += unguarded_refusals(path.read_text(), rel)
    offenders = [o for o in offenders if o not in ALLOWED]
    assert not offenders, (
        'Empty-bodied 2xx refusal flashes swap "" into the control\'s target and '
        "blank it (#684). Return htmx_refusal(body) instead, or HX-Retarget the "
        f"form row: {offenders}"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'HTMLResponse(content="", status_code=200, headers=flash_trigger("warning", "x"))',
        'HTMLResponse("", headers=flash_trigger("warning", "x"))',
        'HTMLResponse(headers={**flash_trigger("error", "x"), "X-Other": "1"})',
    ],
)
def test_sweep_flags_an_unguarded_refusal(snippet):
    assert unguarded_refusals(snippet, "s") == ["s::L1"]


@pytest.mark.parametrize(
    "snippet",
    [
        # the guarded shapes
        'HTMLResponse("", headers={**flash_trigger("warning", "x"), "HX-Reswap": "none"})',
        'HTMLResponse("", headers={**flash_trigger("warning", "x"), "HX-Retarget": "#r"})',
        # a success delete is meant to swap the row away
        'HTMLResponse(content="", headers=flash_trigger("success", "Removed."))',
        # htmx does not swap a 4xx or a 204
        'HTMLResponse(content="", status_code=409, headers=flash_trigger("warning", "x"))',
        'Response(status_code=204, headers=flash_trigger("warning", "x"))',
        # a body is something to swap in, by design
        'HTMLResponse("<tr></tr>", headers=flash_trigger("warning", "x"))',
    ],
)
def test_sweep_passes_a_guarded_or_non_refusal_response(snippet):
    assert unguarded_refusals(snippet, "s") == []


def test_htmx_refusal_flashes_a_warning_and_swaps_nothing():
    r = htmx_refusal("Cannot remove the only name.")
    assert r.status_code == 200
    assert r.body == b""
    assert r.headers["HX-Reswap"] == "none"
    flash = json.loads(r.headers["HX-Trigger"])["showFlash"]
    assert flash == {"level": "warning", "body": "Cannot remove the only name."}
