"""Pagination utilities for admin list views."""

import math


def pagination_context(page: int, count: int, page_size: int) -> dict:
    """Compute pagination context values for a list view.

    Clamps page to the valid range [1, total_pages] when results exist.
    Returns a dict suitable for merging into a template context.
    """
    total_pages = math.ceil(count / page_size) if count > 0 else 0
    if total_pages > 0:
        page = min(page, total_pages)
    return {
        "page": page,
        "total_pages": total_pages,
        "showing_from": (page - 1) * page_size + 1 if count > 0 else 0,
        "showing_to": min(page * page_size, count),
        "page_range": pagination_pages(page, total_pages),
    }


def pagination_pages(page: int, total_pages: int) -> list[int | None]:
    """Return page numbers to display, with None representing ellipsis gaps.

    Always includes the first and last page, the current page, and up to two
    neighbours on each side. None is inserted where a gap of more than one
    page exists between consecutive shown pages.
    """
    if total_pages <= 0:
        return []
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    shown: set[int] = {1, total_pages}
    for p in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        shown.add(p)
    result: list[int | None] = []
    prev = None
    for p in sorted(shown):
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result
