"""Shared HTML-slicing helpers for admin page assertions.

Detail pages carry many independent widgets; asserting on the whole page makes
tests flip red when unrelated markup adopts the asserted string (#341 CR1,
finding 6). Slice down to the relevant table first.
"""


def table_html(page: str, table_id: str) -> str:
    """Return one table's markup from a rendered page (from its id to ``</table>``)."""
    return page.partition(f'id="{table_id}"')[2].partition("</table>")[0]
