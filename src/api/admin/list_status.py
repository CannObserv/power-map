"""Shared status-axis helpers for admin list queries (#306).

Every admin list (orgs / people / roles / jurisdictions) filters by a status
axis whose default (``active``) used to silently hide search matches sitting
under another status — the dedup-hunting trap of #306. Each ``*_queries.py``
module now declares its axis as a ``STATUS_PREDICATES`` dict (status → SQL
predicate) plus the virtual ``all`` status, and calls
:func:`count_with_hidden_matches` when a search is active so the list can
surface "N more matches" instead of dropping them.
"""


async def count_with_hidden_matches(
    db,
    *,
    from_clause: str,
    search_conditions: list[str],
    params: list,
    predicates: dict[str, str],
    status: str,
) -> tuple[int, list[dict]]:
    """Count search matches per status in one grouped pass.

    Returns ``(count, hidden_matches)`` — ``count`` is the match count under
    the *current* ``status`` (or the grand total for ``all``), and
    ``hidden_matches`` is a list of ``{"status", "count"}`` dicts for the
    other statuses that hold matches (empty for ``all``: nothing is hidden).

    ``search_conditions`` / ``params`` are the non-status filter conditions
    (search text, type filter, …) with ``$n`` placeholders already numbered;
    status predicates carry no parameters so the numbering is unaffected.
    """
    selects = ", ".join(
        f'count(*) FILTER (WHERE {pred}) AS "{s}"' for s, pred in predicates.items()
    )
    where = ("WHERE " + " AND ".join(search_conditions)) if search_conditions else ""
    counts = await db.fetchrow(f"SELECT {selects} FROM {from_clause} {where}", *params)
    if status == "all":
        return sum(counts[s] for s in predicates), []
    hidden = [{"status": s, "count": counts[s]} for s in predicates if s != status and counts[s]]
    return counts[status], hidden
