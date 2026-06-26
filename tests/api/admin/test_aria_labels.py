"""Static linting for admin-dashboard accessibility (WCAG 2.1 AA).

Two checks:

1. ``test_read_row_buttons_have_aria_labels`` — every ``btn--sm`` in read-row
   templates must have ``aria-label`` (SC 2.4.6 / 4.1.2: disambiguates repeated
   "Edit"/"Delete" labels across rows on the same page). Form rows (Save/Cancel)
   are excluded — only one form row is open at a time, so there is no
   disambiguation issue on those buttons.

2. ``test_form_controls_have_accessible_names`` — every ``<input>`` (except
   ``type="hidden"``), ``<select>`` and ``<textarea>`` must carry a programmatic
   accessible name (SC 1.3.1 / 4.1.2). A ``placeholder`` is NOT a label. Valid
   mechanisms: ``aria-label`` / ``aria-labelledby`` on the control, an ``id``
   targeted by a ``<label for>``, or being wrapped in a ``<label>``. See
   ``docs/STYLE.md §12 → Form labels``.
"""

import re
from pathlib import Path

_TEMPLATE_BASE = Path("src/templates/admin")

# Auto-discover all read-row partials (singular *_row.html and plural *_rows.html).
# Excludes form/edit rows (Save/Cancel buttons are exempt — only one row is editable
# at a time) and confirm modals.
_READ_ROW_TEMPLATES = sorted(
    p.relative_to(_TEMPLATE_BASE)
    for p in _TEMPLATE_BASE.rglob("*.html")
    if re.search(r"_rows?\.html$", p.name)
    and "_form_row" not in p.name
    and "_edit_row" not in p.name
)

# Quoted-string-aware tag match: handles attribute values that contain >.
# Stops at the first > that is not inside a single- or double-quoted attribute.
_TAG_RE = re.compile(
    r'<(?:button|a)\b(?:[^>"\']*|"[^"]*"|\'[^\']*\')*>',
    re.DOTALL,
)


def _buttons_missing_aria(text: str) -> list[str]:
    return [
        m.group(0)
        for m in _TAG_RE.finditer(text)
        if "btn--sm" in m.group(0) and "aria-label" not in m.group(0)
    ]


def test_read_row_buttons_have_aria_labels():
    """btn--sm in read-row templates must have aria-label."""
    failures: dict[str, list[str]] = {}
    for rel in _READ_ROW_TEMPLATES:
        text = (_TEMPLATE_BASE / rel).read_text()
        missing = _buttons_missing_aria(text)
        if missing:
            failures[rel] = missing

    if failures:
        lines: list[str] = []
        for rel, tags in failures.items():
            lines.append(f"  {rel}:")
            for tag in tags:
                lines.append(f"    {tag[:140]!r}")
        raise AssertionError("btn--sm elements missing aria-label:\n" + "\n".join(lines))


# --- Form-control accessible names --------------------------------------------

# Every admin template may host form controls; not just rows.
_ALL_TEMPLATES = sorted(p.relative_to(_TEMPLATE_BASE) for p in _TEMPLATE_BASE.rglob("*.html"))

# Quoted-string-aware match for the three labelable form controls.
_CONTROL_RE = re.compile(
    r'<(?:input|select|textarea)\b(?:[^>"\']*|"[^"]*"|\'[^\']*\')*>',
    re.DOTALL,
)
# Capture every <label for="..."> target in a file.
_LABEL_FOR_RE = re.compile(r'<label\b[^>]*\bfor="([^"]*)"')
_ID_RE = re.compile(r'\bid="([^"]*)"')


def _has_accessible_name(tag: str, pos: int, text: str, labeled_ids: set[str]) -> bool:
    """True if a control tag has a programmatic accessible name.

    placeholder is intentionally NOT accepted.
    """
    if "aria-label=" in tag or "aria-labelledby=" in tag:
        return True
    id_match = _ID_RE.search(tag)
    if id_match and id_match.group(1) in labeled_ids:
        return True
    # Wrapped in a <label>…</label>: more <label opens than </label> closes before pos.
    before = text[:pos]
    if len(re.findall(r"<label\b", before)) > before.count("</label>"):
        return True
    return False


def _controls_missing_name(text: str) -> list[str]:
    labeled_ids = set(_LABEL_FOR_RE.findall(text))
    missing: list[str] = []
    for m in _CONTROL_RE.finditer(text):
        tag = m.group(0)
        if 'type="hidden"' in tag:
            continue
        if not _has_accessible_name(tag, m.start(), text, labeled_ids):
            missing.append(tag)
    return missing


def test_form_controls_have_accessible_names():
    """input/select/textarea must have a label, aria-label, or aria-labelledby."""
    failures: dict[str, list[str]] = {}
    for rel in _ALL_TEMPLATES:
        text = (_TEMPLATE_BASE / rel).read_text()
        missing = _controls_missing_name(text)
        if missing:
            failures[str(rel)] = missing

    if failures:
        lines: list[str] = []
        for rel, tags in sorted(failures.items()):
            lines.append(f"  {rel}:")
            for tag in tags:
                lines.append(f"    {' '.join(tag.split())[:140]!r}")
        raise AssertionError(
            "form controls missing accessible name (placeholder is not a label):\n"
            + "\n".join(lines)
        )
