"""Public API: observation / upsert endpoint."""

from fastapi import APIRouter, Depends

from src.api.deps import get_db
from src.api.public.deps import AuthedKey, require_scope
from src.api.public.schemas import ObservationRequest, ObservationResponse
from src.core.observation import (
    Disposition,
    IdentifierConflict,
    ObservationRejected,
    resolve_entity,
    write_addresses,
    write_contact_methods,
    write_links,
    write_names,
    write_org_acronyms,
    write_org_parent,
    write_pronouns,
    write_role_assignments,
)

router = APIRouter()

_REJECTED = ObservationResponse(disposition="rejected", entity_id=None, entity_type=None)


@router.post(
    "/observations",
    response_model=ObservationResponse,
    operation_id="submit_observation",
)
async def submit_observation(
    request: ObservationRequest,
    auth: AuthedKey = Depends(require_scope("observations:write")),
    db=Depends(get_db),
) -> ObservationResponse:
    """Submit an identity observation; attach to existing entity or create a new one."""
    entity_id, entity_type, disposition = await resolve_entity(
        db, request.identifier_type, request.identifier_value
    )

    if disposition is Disposition.REJECTED:
        return _REJECTED

    try:
        async with db.transaction():
            await write_names(db, entity_id, entity_type, auth.key_id, request.names)
            await write_links(db, entity_id, entity_type, request.links)
            await write_contact_methods(db, entity_id, entity_type, request.contact_methods)
            await write_addresses(db, entity_id, entity_type, request.addresses)

            if entity_type == "organization":
                await write_org_acronyms(db, entity_id, request.org_acronyms)

                parent_id: str | None = None
                if request.organization_parent_id:
                    parent_id = request.organization_parent_id
                elif request.organization_parent_name:
                    rows = await db.fetch(
                        """SELECT o.id FROM organizations o
                           JOIN organization_names n ON n.organization_id = o.id
                           WHERE n.name = $1 AND n.is_canonical = TRUE
                             AND o.archived_at IS NULL""",
                        request.organization_parent_name,
                    )
                    if len(rows) != 1:
                        return _REJECTED
                    parent_id = rows[0]["id"]
                elif request.organization_parent_acronym:
                    rows = await db.fetch(
                        """SELECT o.id FROM organizations o
                           JOIN organization_acronyms a ON a.organization_id = o.id
                           WHERE a.acronym = $1 AND a.is_canonical = TRUE
                             AND o.archived_at IS NULL""",
                        request.organization_parent_acronym,
                    )
                    if len(rows) != 1:
                        return _REJECTED
                    parent_id = rows[0]["id"]

                if parent_id:
                    await write_org_parent(db, entity_id, parent_id)

            elif entity_type == "person":
                await write_role_assignments(db, entity_id, request.role_assignments)
                if request.personal_pronouns:
                    await write_pronouns(db, entity_id, request.personal_pronouns)

    except ObservationRejected:
        return _REJECTED
    except IdentifierConflict:
        return _REJECTED

    return ObservationResponse(
        disposition=disposition.value,
        entity_id=entity_id,
        entity_type=entity_type,
    )
