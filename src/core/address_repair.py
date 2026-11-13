"""Re-standardize one ``addresses`` row and fold it into a duplicate it now matches (#595).

A row saved while address-validator was down keeps its submitted lines and no
standardized form (``standardization_status='unavailable'``). Once the validator
answers, the row takes the standardized form with Accept semantics — the lines,
the form, lat/long and components — exactly as a curator's Accept would.

**The collision.** ``write_addresses`` dedups on ``COALESCE(standardized,
raw_input)`` per entity and address type, so an entity re-sent after recovery
already holds a second, standardized row B for the same place. Once this row (A)
standardizes to B's form, each of A's links moves onto B:

- the same window already on B (the ``entity_addresses_entity_addr_uniq`` key) →
  A's link is a true duplicate and is deleted;
- otherwise A's link is re-pointed at B, its window unchanged — two windows of
  one address are history, never merged (#181/#256).

A is deleted only once no link and no event place still references it: the
event FK is ``ON DELETE SET NULL``, so deleting a referenced row would silently
blank an event's place. B is the first match by ``(created_at, id)``, the order
``write_addresses`` reuses rows in.

Callers own the transaction: one per address, so a failure leaves no half-merge.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

import asyncpg

from src.core.normalizers.address import standardization_status
from src.core.normalizers.base import NormalizationResult

# The columns a sweep needs from a candidate row.
CANDIDATE_COLUMNS = (
    "id, raw_input, address_line_1, address_line_2, city, region, postal_code, country,"
    " standardization_status, standardization_checked_at"
)


@dataclass(frozen=True)
class RepairOutcome:
    """What ``apply_result`` did to one address row."""

    address_id: str
    status: str
    links_moved: int = 0
    links_dropped: int = 0
    deleted: bool = False


def compose_input(row: Mapping) -> str:
    """The string to re-standardize: ``raw_input``, else the lines joined.

    Admin-written rows have no ``raw_input``; their lines are joined the way the
    admin confirm step joins a submitted form.
    """
    if row["raw_input"]:
        return row["raw_input"]
    parts = (
        row["address_line_1"],
        row["address_line_2"],
        row["city"],
        row["region"],
        row["postal_code"],
    )
    return " ".join(p.strip() for p in parts if p and p.strip())


async def apply_result(
    conn: asyncpg.Connection, address_id: str, result: NormalizationResult
) -> RepairOutcome:
    """Record a fresh normalizer result on the row; merge if it standardized onto a twin.

    A result that still has no form updates only the status and ``checked_at``,
    which the touch trigger ignores, so no parent's ETag moves. An unchanged
    status writes nothing (#797): ``checked_at`` records when the status was
    decided, so the sweep's audit measures how long a row has been stuck.
    """
    status = standardization_status(result)
    if status != "standardized":
        await conn.execute(
            "UPDATE addresses SET standardization_status=$2, standardization_checked_at=NOW()"
            " WHERE id=$1 AND standardization_status IS DISTINCT FROM $2",
            address_id,
            status,
        )
        return RepairOutcome(address_id, status)
    v = result.value
    components = v.get("components")
    await conn.execute(
        "UPDATE addresses SET"
        " address_line_1 = COALESCE($2, address_line_1),"
        " address_line_2 = COALESCE($3, address_line_2),"
        " city = COALESCE($4, city), region = COALESCE($5, region),"
        " postal_code = COALESCE($6, postal_code), country = COALESCE($7, country),"
        " standardized = $8, latitude = $9, longitude = $10, components = $11,"
        " standardization_status = 'standardized', standardization_checked_at = NOW()"
        " WHERE id = $1",
        address_id,
        v.get("address_line_1") or None,
        v.get("address_line_2") or None,
        v.get("city") or None,
        v.get("region") or None,
        v.get("postal_code") or None,
        v.get("country") or None,
        v["standardized"],
        v.get("latitude"),
        v.get("longitude"),
        json.dumps(components) if components else None,
    )
    moved, dropped = await merge_into_twins(conn, address_id, v["standardized"])
    deleted = bool(moved or dropped) and await _delete_if_unreferenced(conn, address_id)
    return RepairOutcome(address_id, status, moved, dropped, deleted)


async def find_twins(conn: asyncpg.Connection, address_id: str, form: str) -> list[dict]:
    """Each link of the row whose entity + address type already holds ``form`` elsewhere.

    Returns ``{link_id, twin_id, duplicate}`` per collision; ``duplicate`` is True when
    the twin already carries the link's exact window. Read-only, so a dry run can
    preview the merge.
    """
    rows = await conn.fetch(
        "SELECT l.id AS link_id, t.address_id AS twin_id,"
        "  EXISTS (SELECT 1 FROM entity_addresses d"
        "          WHERE d.entity_type = l.entity_type AND d.entity_id = l.entity_id"
        "            AND d.address_type = l.address_type AND d.address_id = t.address_id"
        "            AND d.valid_from IS NOT DISTINCT FROM l.valid_from"
        "            AND d.valid_until IS NOT DISTINCT FROM l.valid_until) AS duplicate"
        " FROM entity_addresses l"
        " CROSS JOIN LATERAL ("
        "   SELECT ea.address_id FROM entity_addresses ea"
        "   JOIN addresses a ON a.id = ea.address_id"
        "   WHERE ea.entity_type = l.entity_type AND ea.entity_id = l.entity_id"
        "     AND ea.address_type = l.address_type AND ea.address_id <> l.address_id"
        "     AND COALESCE(a.standardized, a.raw_input) = $2"
        "   ORDER BY ea.created_at, ea.id LIMIT 1"
        " ) t"
        " WHERE l.address_id = $1"
        " ORDER BY l.id",
        address_id,
        form,
    )
    return [dict(r) for r in rows]


async def merge_into_twins(conn: asyncpg.Connection, address_id: str, form: str) -> tuple[int, int]:
    """Move or drop each colliding link of the row; return ``(moved, dropped)``."""
    moved = dropped = 0
    for twin in await find_twins(conn, address_id, form):
        if twin["duplicate"]:
            await conn.execute("DELETE FROM entity_addresses WHERE id=$1", twin["link_id"])
            dropped += 1
        else:
            await conn.execute(
                "UPDATE entity_addresses SET address_id=$2 WHERE id=$1",
                twin["link_id"],
                twin["twin_id"],
            )
            moved += 1
    return moved, dropped


async def _delete_if_unreferenced(conn: asyncpg.Connection, address_id: str) -> bool:
    """Delete the row once no link and no event place references it."""
    status = await conn.execute(
        "DELETE FROM addresses a WHERE a.id = $1"
        " AND NOT EXISTS (SELECT 1 FROM entity_addresses ea WHERE ea.address_id = a.id)"
        " AND NOT EXISTS (SELECT 1 FROM entity_events ev WHERE ev.event_place_address_id = a.id)",
        address_id,
    )
    return status == "DELETE 1"
