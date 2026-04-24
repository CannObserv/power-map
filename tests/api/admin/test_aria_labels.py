"""Static linting: every btn--sm in read-row templates must have aria-label.

WCAG 2.1 AA SC 2.4.6 / 4.1.2: disambiguates repeated action labels
("Edit", "Delete") across rows on the same page.

Form rows (Save/Cancel) are excluded — only one form row is open at a time,
so there is no disambiguation issue on those buttons.
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
        raise AssertionError(
            "btn--sm elements missing aria-label:\n" + "\n".join(lines)
        )
