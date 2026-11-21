"""Which API key wrote a row (#847): the label join every name-row query shares.

`person_names.source_key_id` and `organization_names.source_key_id` record the
key that inserted a name (#162). A row read for display splices in
`source_key_label_lateral(...)` so every render path of a name row — first
render, tbody re-render, single-row read-row — carries the same
`source_key_label`, the way `citation_count_lateral` carries the #341 count.
"""


def source_key_label_lateral(stamp_expr: str, *, alias: str = "sk") -> str:
    """SQL fragment adding ``source_key_label`` (the writing key's label, or NULL).

    NULL when the row has no stamp: an admin, importer or applier write, or a
    key since deleted (`ON DELETE SET NULL`). A ``SELECT *`` query picks the
    column up as is; an aliased ``pn.*`` query selects ``<alias>_j.source_key_label``.

    ``stamp_expr`` is interpolated verbatim: it must be a code-supplied column
    expression (e.g. ``"pn.source_key_id"``), never user input.
    """
    return (
        f" LEFT JOIN LATERAL ("
        f"SELECT {alias}.label AS source_key_label FROM api_keys {alias}"
        f" WHERE {alias}.id = {stamp_expr}) {alias}_j ON TRUE "
    )
