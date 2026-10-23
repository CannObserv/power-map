"""Static sweep: multi-value detail tables re-render their whole tbody (#683).

The behavioural half — add the first item, delete the last, a bad save — is
``test_empty_state_tables.py``. This half pins the contract on every template
and route so a new table can't quietly regress to per-row swaps:

1. A write control (``hx-post``/``hx-delete``/``hx-put``/``hx-patch``) never
   targets a single row (``#…-row-…`` / ``closest tr``): a per-row swap can't
   add or drop the empty-state row, so the table desyncs from the server.
2. A table's ``{% else %}`` empty-state row carries ``data-empty-row``, the
   stable marker tests and scripts key on.
3. A detail page never inlines an empty-state row: it includes the same
   ``_<x>_rows.html`` partial the write routes render, so first paint and every
   write can't drift apart.
4. A write route rendering a form row (an error re-render) retargets it onto
   that row (``retarget_form_row`` / ``form_row_status``) — the form itself
   targets the tbody — and no form-row render carries a literal 4xx: htmx
   swaps no 4xx, so the curator would never see the error.

Rules → ``docs/UI.md`` § Empty-State Table Rows.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TEMPLATES = REPO / "src" / "templates" / "admin"
ADMIN_DIR = REPO / "src" / "api" / "admin"

# An opening tag, tolerating Jinja expressions/statements/comments inside it.
_TAG = re.compile(
    r"<(form|button|a|tr|input|select)\b(?:[^>{]|\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\})*>", re.S
)
_WRITE = re.compile(r"\bhx-(post|delete|put|patch)=")
_TARGET = re.compile(r'hx-target="([^"]*)"')
_ROW_TARGET = re.compile(r"-row-|closest tr")
_EMPTY_ROW = re.compile(r"\{%-?\s*else\s*-?%\}\s*(<tr\b[^>]*>)")

# Row-swapping write controls still allowed: path relative to src/templates/admin
# -> why. The settings tables have the same empty-row bug and are deferred to #687;
# drop their entries as that lands.
ROW_TARGET_ALLOWED: dict[str, str] = {
    "partials/delete_modal.html": "list-page hard delete; the list re-paginates",
    "pins/_row.html": "unpin keeps the row (re-rendered as Unpinned), never empties the table",
    "settings/partials/_api_key_row.html": "deferred: #687",
    "settings/partials/_api_key_edit_row.html": "deferred: #687",
    "settings/partials/_identifier_type_row.html": "deferred: #687",
    "settings/partials/_identifier_type_edit_row.html": "deferred: #687",
    "settings/partials/_link_type_row.html": "deferred: #687",
    "settings/partials/_link_type_edit_row.html": "deferred: #687",
}

_FORM_ROW_HELPERS = ("retarget_form_row(", "form_row_status(", "HX-Retarget")
_MUTATING = {"post", "delete", "put", "patch"}


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _line(text: str, offset: int) -> int:
    return text[:offset].count("\n") + 1


def test_write_controls_never_target_a_single_row():
    offenders = []
    for path in _templates():
        rel = path.relative_to(TEMPLATES).as_posix()
        if rel in ROW_TARGET_ALLOWED:
            continue
        text = path.read_text()
        for m in _TAG.finditer(text):
            tag = m.group(0)
            if not _WRITE.search(tag):
                continue
            rows = [t for t in _TARGET.findall(tag) if _ROW_TARGET.search(t)]
            if rows:
                offenders.append(f"{rel}:{_line(text, m.start())} {rows}")
    assert not offenders, (
        "A write control swaps a single row, so the table's empty-state row can't "
        "follow (#683). Target the whole tbody (`#<x>-table tbody`, innerHTML) and "
        f"render the `_<x>_rows.html` partial: {offenders}"
    )


def test_row_target_allowlist_is_live():
    stale = [rel for rel in ROW_TARGET_ALLOWED if not (TEMPLATES / rel).exists()]
    assert not stale, f"ROW_TARGET_ALLOWED names templates that no longer exist: {stale}"


def test_empty_state_rows_carry_the_marker():
    offenders = []
    for path in _templates():
        text = path.read_text()
        for m in _EMPTY_ROW.finditer(text):
            if "data-empty-row" not in m.group(1):
                rel = path.relative_to(TEMPLATES).as_posix()
                offenders.append(f"{rel}:{_line(text, m.start())}")
    assert not offenders, (
        f"An `{{% else %}}` empty-state <tr> lacks `data-empty-row` (#683): {offenders}"
    )


def test_detail_pages_include_rows_partials():
    offenders = []
    for path in sorted(TEMPLATES.glob("*/detail.html")):
        text = path.read_text()
        for m in _EMPTY_ROW.finditer(text):
            offenders.append(f"{path.relative_to(TEMPLATES).as_posix()}:{_line(text, m.start())}")
    assert not offenders, (
        "A detail page inlines a table's empty-state row; include the "
        "`_<x>_rows.html` partial the write routes render instead (#683): "
        f"{offenders}"
    )


def _template_name(call: ast.Call) -> str | None:
    arg = call.args[1] if len(call.args) > 1 else None
    if arg is None:
        return None
    return arg.value if isinstance(arg, ast.Constant) else ast.unparse(arg)


def _is_form_row(name: str | None) -> bool:
    return bool(name) and ("form_row" in name or "edit_row" in name)


def _form_row_renders():
    """(file, function node, TemplateResponse call) for every form-row render."""
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "TemplateResponse"
                    and _is_form_row(_template_name(node))
                ):
                    yield path.name, fn, node


def _route_verbs(fn) -> set[str]:
    return {
        d.func.attr
        for d in fn.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    }


def test_write_route_form_row_errors_retarget_the_row():
    offenders = [
        f"{name}:{call.lineno} {fn.name}"
        for name, fn, call in _form_row_renders()
        if _MUTATING & _route_verbs(fn)
        and not any(h in ast.unparse(call) for h in _FORM_ROW_HELPERS)
    ]
    assert not offenders, (
        "A write route renders a form row without retargeting it: the form posts "
        "into the tbody, so the error row would replace every row (#683). Pass "
        f"headers=retarget_form_row(<row id>): {offenders}"
    )


def test_no_form_row_render_carries_a_literal_4xx():
    offenders = []
    for name, fn, call in _form_row_renders():
        for kw in call.keywords:
            if (
                kw.arg == "status_code"
                and isinstance(kw.value, ast.Constant)
                and 400 <= kw.value.value < 500
            ):
                offenders.append(f"{name}:{call.lineno} {fn.name}")
    assert not offenders, (
        "A form-row render answers 4xx: htmx swaps no 4xx, so the curator never "
        "sees the error. Use form_row_status(...) — 200 + retarget for HTMX, the "
        f"4xx for everyone else (#683): {offenders}"
    )


def test_sweeps_see_the_known_tables():
    """Guard the sweeps themselves: they must find real write controls and renders."""
    writes = sum(
        1
        for path in _templates()
        for m in _TAG.finditer(path.read_text())
        if _WRITE.search(m.group(0)) and " tbody" in m.group(0)
    )
    assert writes >= 30, writes
    assert sum(1 for _ in _form_row_renders()) >= 20
