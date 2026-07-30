"""Shared HTML-slicing helpers for admin page assertions.

Detail pages carry many independent widgets; asserting on the whole page makes
tests flip red when unrelated markup adopts the asserted string (#341 CR1,
finding 6). Slice down to the relevant table first.
"""


def table_html(page: str, table_id: str) -> str:
    """Return one table's markup from a rendered page (from its id to ``</table>``).

    Fails loudly when ``table_id`` is absent — a silent empty slice would let
    negative assertions (``"…" not in table_html(...)``) pass vacuously after a
    table-id rename (#341 CR3, finding 10).
    """
    _before, sep, rest = page.partition(f'id="{table_id}"')
    assert sep, f"table id {table_id!r} not found in page"
    return rest.partition("</table>")[0]
