"""Admin CRUD for organization names."""

from src.api.admin._names_shared import make_names_router
from src.api.admin.deps import org_header_extra


async def _maybe_promote_sole_name(org_id: str, db) -> None:
    """If the org has exactly one name and it is not canonical, promote it."""
    rows = await db.fetch(
        "SELECT id, is_canonical FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    if len(rows) == 1 and not rows[0]["is_canonical"]:
        await db.execute(
            "UPDATE organization_names SET is_canonical=TRUE WHERE id=$1",
            rows[0]["id"],
        )


async def _last_identity_blocked(org_id: str, db) -> bool:
    """Return True when deleting would remove the last name with no canonical acronym."""
    name_count = await db.fetchval(
        "SELECT count(*) FROM organization_names WHERE organization_id=$1",
        org_id,
    )
    canonical_acronym_count = await db.fetchval(
        "SELECT count(*) FROM organization_acronyms"
        " WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    return name_count == 1 and canonical_acronym_count == 0


router = make_names_router(
    entity_id_key="org_id",
    prefix="/orgs/{entity_id}/names",
    tags=["admin-org-names"],
    entity_table="organizations",
    entity_not_found_msg="Organization not found",
    names_table="organization_names",
    entity_fk="organization_id",
    tmpl_form_row="admin/orgs/partials/_name_form_row.html",
    tmpl_read_row="admin/orgs/partials/_name_row.html",
    tmpl_rows="admin/orgs/partials/_name_rows.html",
    detail_url=lambda eid: f"/admin/orgs/{eid}/",
    maybe_promote_sole_name=_maybe_promote_sole_name,
    last_identity_blocked=_last_identity_blocked,
    last_identity_error_msg=(
        "Cannot remove the only name when the organization has no canonical acronym."
    ),
    last_identity_409_msg="Cannot remove the only name: no canonical acronym exists.",
    header_extra=org_header_extra,
)
