"""Static assertions for people list template correctness."""
from pathlib import Path

REGION_HTML = Path("src/templates/admin/people/_region.html").read_text()


# ---------------------------------------------------------------------------
# List page — pagination placement
# ---------------------------------------------------------------------------


def test_people_region_has_single_pagination_call():
    """Top pagination was removed; only the sticky call should remain.

    Two calls would render a redundant pagination bar above the table.
    """
    assert REGION_HTML.count("pagination(") == 1
