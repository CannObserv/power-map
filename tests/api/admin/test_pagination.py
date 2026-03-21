"""Unit tests for admin pagination utility."""

from src.api.admin.pagination import pagination_context, pagination_pages


# --- pagination_context ---


def test_context_first_page():
    ctx = pagination_context(1, 100, 50)
    assert ctx["page"] == 1
    assert ctx["total_pages"] == 2
    assert ctx["showing_from"] == 1
    assert ctx["showing_to"] == 50


def test_context_last_page():
    ctx = pagination_context(2, 100, 50)
    assert ctx["page"] == 2
    assert ctx["showing_from"] == 51
    assert ctx["showing_to"] == 100


def test_context_partial_last_page():
    ctx = pagination_context(2, 75, 50)
    assert ctx["showing_from"] == 51
    assert ctx["showing_to"] == 75


def test_context_empty_result():
    ctx = pagination_context(1, 0, 50)
    assert ctx["total_pages"] == 0
    assert ctx["showing_from"] == 0
    assert ctx["showing_to"] == 0
    assert ctx["page_range"] == []


def test_context_page_clamped_when_beyond_total():
    ctx = pagination_context(5, 60, 50)
    assert ctx["page"] == 2
    assert ctx["showing_from"] == 51
    assert ctx["showing_to"] == 60


def test_context_page_not_clamped_when_empty():
    # No clamping when total_pages == 0; page stays as provided
    ctx = pagination_context(3, 0, 50)
    assert ctx["page"] == 3
    assert ctx["showing_from"] == 0
    assert ctx["showing_to"] == 0


def test_context_page_range_uses_clamped_page():
    # page=99 with only 3 pages → clamped to 3 → page_range centred on 3
    ctx = pagination_context(99, 150, 50)
    assert ctx["page"] == 3
    assert 3 in ctx["page_range"]


# --- pagination_pages ---


def test_empty_when_no_pages():
    assert pagination_pages(1, 0) == []


def test_single_page():
    assert pagination_pages(1, 1) == [1]


def test_all_pages_when_seven_or_fewer():
    assert pagination_pages(1, 7) == [1, 2, 3, 4, 5, 6, 7]
    assert pagination_pages(4, 7) == [1, 2, 3, 4, 5, 6, 7]


def test_ellipsis_at_end_near_start():
    result = pagination_pages(1, 20)
    assert result[0] == 1
    assert None in result
    assert result[-1] == 20


def test_ellipsis_at_start_near_end():
    result = pagination_pages(20, 20)
    assert result[0] == 1
    assert None in result
    assert result[-1] == 20


def test_ellipsis_both_sides_in_middle():
    result = pagination_pages(10, 20)
    assert result[0] == 1
    assert result[-1] == 20
    assert result.count(None) == 2


def test_neighbours_shown():
    result = pagination_pages(10, 20)
    for p in [8, 9, 10, 11, 12]:
        assert p in result


def test_no_ellipsis_when_gap_is_one():
    # page=3, total=8: shown={1,2,3,4,5,8} → [1,2,3,4,5,None,8]
    assert pagination_pages(3, 8) == [1, 2, 3, 4, 5, None, 8]


def test_near_start_single_ellipsis():
    # page=1, total=8: shown={1,2,3,8} → [1,2,3,None,8]
    assert pagination_pages(1, 8) == [1, 2, 3, None, 8]


def test_near_end_single_ellipsis():
    # page=8, total=8: shown={1,6,7,8} → [1,None,6,7,8]
    assert pagination_pages(8, 8) == [1, None, 6, 7, 8]
