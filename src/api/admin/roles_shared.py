"""Shared helpers for role detail and assignment inline routes.

Extracted here to break the circular import that would arise if
roles_detail.py and roles_assignments_inline.py imported from each other.
Both modules import from this one instead.
"""

import datetime

from fastapi import HTTPException


def _parse_date(value: str) -> datetime.date | None:
    """Parse ISO date string, return None if empty."""
    value = value.strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def _check_assignment_within_bounds(
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    established_on: datetime.date | None,
    abolished_on: datetime.date | None,
) -> str | None:
    """Return an error string if dates violate role boundaries, else None."""
    if established_on is not None:
        if start_date is not None and start_date < established_on:
            return f"Start date cannot be before role established date ({established_on})."
        if end_date is not None and end_date < established_on:
            return f"End date cannot be before role established date ({established_on})."
    if abolished_on is not None:
        if start_date is not None and start_date > abolished_on:
            return f"Start date cannot be after role abolished date ({abolished_on})."
        if end_date is not None and end_date > abolished_on:
            return f"End date cannot be after role abolished date ({abolished_on})."
    return None


async def fetch_role_types(db):
    """The role_types catalog for the role-type select (shared by create + inline)."""
    return await db.fetch("SELECT id, slug, display_name FROM role_types ORDER BY display_name")


async def _get_role(role_id: str, db):
    """Fetch role with org display name + structural fields, or raise 404."""
    row = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  r.established_on, r.abolished_on,
                  r.organization_id AS org_id,
                  r.role_type_id, r.jurisdiction_id, r.qualifier,
                  dn.display_name AS org_name,
                  rt.display_name AS role_type_name,
                  rt.slug AS role_type_slug,
                  jdn.display_name AS jurisdiction_name,
                  jdn.slug AS jurisdiction_slug
           FROM roles r
           LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
           LEFT JOIN role_types rt ON rt.id = r.role_type_id
           LEFT JOIN v_jurisdiction_display_names jdn
                  ON jdn.jurisdiction_id = r.jurisdiction_id
           WHERE r.id = $1""",
        role_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")
    return row
