"""Org lifespan bounds for role assignments (#307).

An org's lifespan end is ``v_org_lifespan.ended_on`` (derived from its
``dissolved``/``merged_with`` entity events). Assignment writes that would
violate the lifespan invariant raise :class:`AssignmentOutsideOrgLifespan`;
admin routes turn that into an inline form error. Unknown-end rows
(``is_current=FALSE, end_date NULL``) are allowed — the audit script
(``scripts/audit_org_lifecycle_assignments.py``) warns on those instead.
"""

import datetime

import asyncpg

_ROLE_ENDED_ON_SQL = """
SELECT ls.ended_on
FROM roles r
JOIN v_org_lifespan ls ON ls.organization_id = r.organization_id
WHERE r.id = $1
"""

# Open = end_date IS NULL on a non-archived assignment of a non-archived role.
# Single source for the predicate — admin surfaces count through here.
_OPEN_ASSIGNMENT_COUNT_SQL = """
SELECT count(*)
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.organization_id = $1
  AND ra.archived_at IS NULL AND r.archived_at IS NULL
  AND ra.end_date IS NULL
"""


class AssignmentOutsideOrgLifespan(Exception):
    """Assignment window falls outside its org's lifespan.

    ``code`` matches the audit categories: ``current_on_ended``,
    ``end_after_ended``, ``start_after_ended``.
    """

    def __init__(self, code: str, ended_on: datetime.date):
        self.code = code
        self.ended_on = ended_on
        super().__init__(code)


async def get_org_ended_on(conn: asyncpg.Connection, organization_id: str) -> datetime.date | None:
    """Return the org's lifespan end date, or None when it has not ended."""
    return await conn.fetchval(
        "SELECT ended_on FROM v_org_lifespan WHERE organization_id = $1",
        organization_id,
    )


async def count_open_assignments(conn: asyncpg.Connection, organization_id: str) -> int:
    """Count open (end_date NULL, non-archived) assignments on the org's roles."""
    return await conn.fetchval(_OPEN_ASSIGNMENT_COUNT_SQL, organization_id)


async def check_assignment_lifespan(
    conn: asyncpg.Connection,
    role_id: str,
    *,
    is_current: bool,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
) -> None:
    """Raise AssignmentOutsideOrgLifespan when the write violates #307.

    Precedence mirrors the audit script: a start after the org's end is the
    strongest contradiction, then a late end, then currency on an ended org.
    No-op when the role's org has no lifespan end (or the role is unknown).
    """
    ended_on = await conn.fetchval(_ROLE_ENDED_ON_SQL, role_id)
    if ended_on is None:
        return
    if start_date is not None and start_date > ended_on:
        raise AssignmentOutsideOrgLifespan("start_after_ended", ended_on)
    if end_date is not None and end_date > ended_on:
        raise AssignmentOutsideOrgLifespan("end_after_ended", ended_on)
    if is_current:
        raise AssignmentOutsideOrgLifespan("current_on_ended", ended_on)


def lifespan_error_message(exc: AssignmentOutsideOrgLifespan) -> str:
    """Human-readable form error naming the org's end date."""
    ended = exc.ended_on.isoformat()
    return {
        "current_on_ended": (
            f"This organization ended {ended}; the assignment cannot be current. "
            f"Set an end date on or before {ended}."
        ),
        "end_after_ended": f"End date is after the organization's end ({ended}).",
        "start_after_ended": f"Start date is after the organization's end ({ended}).",
    }[exc.code]
