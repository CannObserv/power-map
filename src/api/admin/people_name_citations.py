"""Admin CRUD for person_name citations (#319).

person_name is a sub-entity (a row on the person detail page), so its citation
panel loads inline via ``inline_panel=True`` into a drawer below the names table.
The non-htmx redirect resolves to the owning person.
"""

from src.api.admin._citations_shared import make_citations_router


async def _resolve_person(name_id: str, db) -> str:
    # Parent lookup for the non-htmx redirect only; no name display (allow-listed
    # in tests/core/test_visible_names_filter.py).
    pid = await db.fetchval("SELECT person_id FROM person_names WHERE id=$1", name_id)
    return f"/admin/people/{pid}/" if pid else "/admin/people/"


router = make_citations_router(
    entity_type="person_name",
    prefix="/person-names/{entity_id}/citations",
    tags=["admin-person-name-citations"],
    entity_table="person_names",
    entity_not_found_msg="Name not found",
    detail_url=lambda eid: "/admin/people/",
    redirect_resolver=_resolve_person,
    inline_panel=True,
)
