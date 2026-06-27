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

3. ``test_optional_placeholder_cue_has_describedby`` — a control that marks
   "(optional)" in its ``placeholder`` must also expose it via
   ``aria-describedby`` (placeholders are unreliable for assistive tech). See
   ``docs/STYLE.md §12 → Optional-field cue``.
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

    Heuristic, per-file, regex/substring-based — accepted limitations (none
    triggered by current templates, verified 2026-06; see GH #244). The
    render-based a11y harness (GH #246) is the authoritative fix for all three;
    #1 alone could also be closed by a single-file ancestry parser (it is
    in-file, not cross-template):

      1. Two controls under one wrapping ``<label>`` both pass, though only the
         first labelable descendant actually receives the accessible name.
         In-file, but needs real ancestry resolution to detect — fails
         *silently* (the second control passes the lint).
      2. A control whose wrapping ``<label>`` lives in a *parent* template (the
         control pulled in via ``{% include %}``) false-positives — this file's
         text never sees the opening ``<label``. Cross-template: the label is in
         another file. Fails *loud* (the control is flagged), so it is
         self-policing.
      3. ``aria-labelledby`` is accepted by presence; a dangling id reference
         (target absent, or rendered by a parent/sibling template) is not
         validated. Cross-template when the target renders elsewhere — fails
         *silently*.
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


# --- Optional-field cue -------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r'placeholder="([^"]*)"')


def _optional_cue_missing_describedby(text: str) -> list[str]:
    """Controls whose placeholder marks '(optional)' but lack aria-describedby.

    placeholder text is unreliable for assistive tech, so the optionality cue
    must also be exposed via aria-describedby -> a .visually-hidden hint.
    See docs/STYLE.md §12 → Optional-field cue.
    """
    missing: list[str] = []
    for m in _CONTROL_RE.finditer(text):
        tag = m.group(0)
        ph = _PLACEHOLDER_RE.search(tag)
        if ph and "optional" in ph.group(1).lower() and "aria-describedby=" not in tag:
            missing.append(tag)
    return missing


def test_optional_placeholder_cue_has_describedby():
    """A placeholder '(optional)' cue must be backed by aria-describedby."""
    failures: dict[str, list[str]] = {}
    for rel in _ALL_TEMPLATES:
        text = (_TEMPLATE_BASE / rel).read_text()
        missing = _optional_cue_missing_describedby(text)
        if missing:
            failures[str(rel)] = missing

    if failures:
        lines: list[str] = []
        for rel, tags in sorted(failures.items()):
            lines.append(f"  {rel}:")
            for tag in tags:
                lines.append(f"    {' '.join(tag.split())[:140]!r}")
        raise AssertionError(
            "placeholder '(optional)' cue without aria-describedby "
            "(see docs/STYLE.md §12 → Optional-field cue):\n" + "\n".join(lines)
        )
