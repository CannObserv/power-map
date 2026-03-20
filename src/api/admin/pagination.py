"""Pagination utilities for admin list views."""


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
