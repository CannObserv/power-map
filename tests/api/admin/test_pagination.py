"""Unit tests for admin pagination utility."""

from src.api.admin.pagination import pagination_pages


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
