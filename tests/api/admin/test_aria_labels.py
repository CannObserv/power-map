"""Static linting: every btn--sm in read-row templates must have aria-label.

WCAG 2.1 AA SC 2.4.6 / 4.1.2: disambiguates repeated action labels
("Edit", "Delete") across rows on the same page.

Form rows (Save/Cancel) are excluded — only one form row is open at a time,
so there is no disambiguation issue on those buttons.
"""
import re
from pathlib import Path

_TEMPLATE_BASE = Path("src/templates/admin")

# Read-row templates: every btn--sm must carry aria-label.
# Does NOT include *_form_row.html or *_edit_row.html (Save/Cancel excluded).
_READ_ROW_TEMPLATES = [
    # org detail subsections
    "orgs/partials/_acronym_row.html",
    "orgs/partials/_address_row.html",
    "orgs/partials/_child_row.html",
    "orgs/partials/_contact_row.html",
    "orgs/partials/_identifier_row.html",
    "orgs/partials/_link_row.html",
    "orgs/partials/_name_row.html",
    # person detail subsections
    "people/partials/_address_row.html",
    "people/partials/_assignment_row.html",
    "people/partials/_contact_row.html",
    "people/partials/_identifier_row.html",
    "people/partials/_link_row.html",
    "people/partials/_name_row.html",
    # role detail subsections
    "roles/partials/_assignment_row.html",
    # settings
    "settings/partials/_api_key_row.html",
    "settings/partials/_identifier_type_row.html",
    "settings/partials/_link_type_row.html",
    # list tables
    "orgs/_rows.html",
    "people/_rows.html",
    "roles/_rows.html",
    "role_assignments/_rows.html",
]

# Matches opening tags of <button> or <a>; stops at first bare > (safe for our
# templates — hx-confirm values never contain unquoted >).
_TAG_RE = re.compile(r"<(?:button|a)\b[^>]*>", re.DOTALL)


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
