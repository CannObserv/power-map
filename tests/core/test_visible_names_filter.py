"""Unit tests for visible_names_filter helper + lint test for direct person_names access."""

import re
from pathlib import Path

from src.core.db import visible_names_filter


def test_visible_names_filter_returns_sql_fragment():
    s = visible_names_filter()
    assert isinstance(s, str)
    assert s.strip() != ""
    assert "visibility" in s
    assert "'public'" in s


def test_visible_names_filter_uses_alias():
    """When called with alias, fragment qualifies the column."""
    s = visible_names_filter(alias="pn")
    assert "pn.visibility" in s


# --- Lint test: forbid raw `FROM person_names` / `JOIN person_names` outside the allow-list ---

ALLOWED_DIRECT_ACCESS = {
    # Files explicitly permitted to query person_names without inline visibility
    # filtering. Each file documents its handling stance in a comment.
    "src/core/db.py",                       # defines the helper
    "src/api/admin/people.py",              # admin detail / hard-delete — surfaces all
    "src/api/admin/people_names.py",        # name-management page — edits all
    "src/api/admin/people_name_suggest.py", # suggest-only decomposition — reads all names
    "src/api/admin/people_merge.py",        # merge logic — touches all rows on both sides
    # _names_shared.py uses dynamic {names_table} f-strings; the regex below
    # never matches verbatim "person_names" there. Listed for reviewer clarity.
    "src/api/admin/_names_shared.py",       # shared admin CRUD via {names_table}
}

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns that *count* as a visibility filter on a person_names access.
# Tighter than substring match — must look like an actual SQL predicate or a
# call to the helper. The first pattern matches both unqualified
# (`visibility = 'public'`) and qualified (`pn.visibility = 'public'`) forms
# because `\b` is a zero-width boundary between `.` and `v`.
_FILTER_PATTERNS = [
    re.compile(r"\bvisibility\s*=\s*'public'", re.IGNORECASE),
    re.compile(r"visible_names_filter\s*\("),
]


def _has_visibility_guard(window_text: str) -> bool:
    return any(p.search(window_text) for p in _FILTER_PATTERNS)


def test_no_unguarded_person_names_queries():
    """No raw `FROM person_names` / `JOIN person_names` outside the allow-list."""
    sql_pattern = re.compile(r"\b(?:FROM|JOIN)\s+person_names\b", re.IGNORECASE)
    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_DIRECT_ACCESS:
            continue
        text = path.read_text()
        lines = text.splitlines()
        for line_no, line in enumerate(lines, start=1):
            if not sql_pattern.search(line):
                continue
            # Inspect a tight window: 2 lines back through 8 lines forward.
            # The window must contain a real visibility predicate or a call
            # to visible_names_filter() — not just the word "visibility".
            start = max(0, line_no - 3)
            end = min(len(lines), line_no + 8)
            window = "\n".join(lines[start:end])
            if not _has_visibility_guard(window):
                offenders.append(f"{rel}:{line_no}")
    assert not offenders, (
        f"Direct `FROM/JOIN person_names` access without a visibility predicate "
        f"or visible_names_filter() call in: {offenders}. Either go through "
        f"v_person_display_names, AND-append `visibility = 'public'`, call "
        f"visible_names_filter(), or add the file to ALLOWED_DIRECT_ACCESS "
        f"with a justification."
    )
