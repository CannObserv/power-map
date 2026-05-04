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
    # Files explicitly permitted to query person_names without the helper.
    # Each file documents its visibility-handling stance in a comment.
    "src/core/db.py",                       # defines the helper
    "src/api/admin/people.py",              # admin detail / hard-delete — surfaces all
    "src/api/admin/people_names.py",        # name-management page — edits all
    "src/api/admin/people_merge.py",        # merge logic — touches all rows on both sides
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_unguarded_person_names_queries():
    """No raw `FROM person_names` / `JOIN person_names` outside the allow-list."""
    pattern = re.compile(r"\b(?:FROM|JOIN)\s+person_names\b", re.IGNORECASE)
    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_DIRECT_ACCESS:
            continue
        text = path.read_text()
        # Skip lines that already include visibility filtering.
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                # Look at this line and a small window of nearby lines for the filter.
                window_start = max(0, line_no - 3)
                window_end = min(len(text.splitlines()), line_no + 5)
                window = "\n".join(text.splitlines()[window_start:window_end])
                if "visibility" not in window:
                    offenders.append(f"{rel}:{line_no}")
    assert not offenders, (
        f"Direct `FROM/JOIN person_names` access without visibility filter in: "
        f"{offenders}. Either go through v_person_display_names, AND-append "
        f"visible_names_filter() or 'visibility = ...' inline, or add the file to "
        f"ALLOWED_DIRECT_ACCESS with a justification."
    )
