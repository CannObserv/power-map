"""Multi-value detail tables re-render their whole ``<tbody>`` on every write (#683).

A table's create, edit and delete controls target ``#<x>-table tbody`` with
``innerHTML``, so the server owns the empty-state row and the order: adding the
first item drops the ``No …`` row, deleting the last brings it back, without a
reload. A form-row error is retargeted onto the row being edited
(``HX-Retarget``), never into the tbody.

One parametrized walk per section: the detail page shows the empty-state row,
create returns the rows without it, edit returns the rows, a bad create lands
on the new-row, and deleting the last item brings the empty-state row back.
The static half (every table, every control) is ``test_empty_state_sweep.py``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.html_slices import table_html

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
EMPTY_MARKER = "data-empty-row"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """Lifespan-less client sharing the rolled-back connection (#288)."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---- parent entities ----------------------------------------------------------


async def _seed_org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _seed_person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _seed_role(db) -> str:
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')",
        rid,
        await _seed_org(db),
    )
    return rid


async def _seed_role_assignment(db) -> str:
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        await _seed_person(db),
        await _seed_role(db),
    )
    return raid


async def _seed_jurisdiction(db) -> str:
    jid = generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id)"
        " SELECT $1, $2, 'Empty-State LD', id FROM jurisdiction_types"
        " WHERE slug = 'legislative_district'",
        jid,
        f"ld-{jid[-8:].lower()}",
    )
    return jid


# entity_type -> (seed, detail-url prefix)
ENTITIES: dict[str, tuple[Callable[..., Awaitable[str]], str]] = {
    "person": (_seed_person, "/admin/people"),
    "organization": (_seed_org, "/admin/orgs"),
    "role": (_seed_role, "/admin/roles"),
    "role_assignment": (_seed_role_assignment, "/admin/role-assignments"),
    "jurisdiction": (_seed_jurisdiction, "/admin/jurisdictions"),
}


# ---- sections -----------------------------------------------------------------

FormData = Callable[..., Awaitable[dict]]


def _const(data: dict) -> FormData:
    async def build(db, eid):
        return dict(data)

    return build


async def _link_data(db, eid, url="https://empty-state.example/one"):
    lt = await db.fetchval("SELECT id FROM link_types ORDER BY is_social, slug LIMIT 1")
    return {"url": url, "link_type_id": lt, "is_active": "true"}


async def _link_edit(db, eid):
    return await _link_data(db, eid, url="https://empty-state.example/two")


def _identifier_data(entity_type: str, value: str) -> FormData:
    async def build(db, eid):
        type_id = await db.fetchval(
            "SELECT id FROM entity_identifier_types"
            " WHERE entity_type=$1 AND NOT is_internal ORDER BY slug LIMIT 1",
            entity_type,
        )
        return {"entity_identifier_type_id": type_id, "value": value}

    return build


@dataclass(frozen=True)
class Section:
    """One multi-value table on one entity's detail page."""

    entity: str
    path: str  # sub-resource path segment, e.g. "contacts"
    table_id: str
    empty_text: str
    row_prefix: str  # read/edit row id prefix, e.g. "contact-row"
    new_row_id: str
    child_sql: str  # $1 = parent id -> the created child's id
    create: FormData
    created_text: str
    edit: FormData | None = None  # None: the section has no edit (link/unlink only)
    edited_text: str = ""
    tbody_id: str | None = None  # the tbody's own id, when it has one; "{eid}" expands
    before_delete: str | None = None  # POST <base>/<id>/<this> first (archive-then-delete)
    invalid_create: FormData | None = None
    invalid_edit: FormData | None = None
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.entity}-{self.table_id}"


def _contact_sections(entity: str) -> list[Section]:
    out = []
    for ctype, table, empty, value, edited, bad in (
        ("email", "emails-table", "No email addresses", "one@es.example", "two@es.example", "x"),
        ("phone", "phones-table", "No phone numbers", "+12065550101", "+12065550102", "12"),
    ):
        out.append(
            Section(
                entity=entity,
                path="contacts",
                table_id=table,
                empty_text=empty,
                row_prefix="contact-row",
                new_row_id=f"{ctype}-row-new",
                child_sql="SELECT id FROM contact_methods WHERE entity_id=$1",
                create=_const({"contact_type": ctype, "value": value}),
                created_text=value,
                edit=_const({"value": edited, "display_label": "Work"}),
                edited_text=edited,
                invalid_create=_const({"contact_type": ctype, "value": bad}),
                invalid_edit=_const({"value": bad}),
            )
        )
    return out


def _link_section(entity: str) -> Section:
    return Section(
        entity=entity,
        path="links",
        table_id="links-table",
        empty_text="No links",
        row_prefix="link-row",
        new_row_id="link-row-new",
        child_sql="SELECT id FROM links WHERE entity_id=$1",
        create=_link_data,
        created_text="https://empty-state.example/one",
        edit=_link_edit,
        edited_text="https://empty-state.example/two",
    )


def _identifier_section(entity: str) -> Section:
    return Section(
        entity=entity,
        path="identifiers",
        table_id="identifiers-table",
        empty_text="No identifiers",
        row_prefix="identifier-row",
        new_row_id="identifier-row-new",
        child_sql="SELECT id FROM identifiers WHERE entity_id=$1",
        create=_identifier_data(entity, "ES-0001"),
        created_text="ES-0001",
        edit=_identifier_data(entity, "ES-0002"),
        edited_text="ES-0002",
    )


async def _named_org(db, name: str) -> str:
    oid = await _seed_org(db)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _aff_type(db) -> str:
    return await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliation_types ORDER BY id LIMIT 1"
    )


async def _child_data(db, eid):
    return {"child_id": await _named_org(db, "Empty-State Child Org")}


async def _jur_aff_data(db, eid):
    org = await _named_org(db, "Empty-State Affiliated Org")
    return {"organization_id": org, "affiliation_type_id": await _aff_type(db)}


async def _org_aff_data(db, eid):
    return {
        "jurisdiction_id": await _seed_jurisdiction(db),
        "affiliation_type_id": await _aff_type(db),
    }


async def _jur_rel_data(db, eid):
    rt = await db.fetchval("SELECT id FROM jurisdiction_relationship_types WHERE slug='borders'")
    return {"target_id": await _seed_jurisdiction(db), "rel_type_id": rt}


async def _ra_rel_data(db, eid):
    rt = await db.fetchval(
        "SELECT id FROM role_assignment_relationship_types WHERE slug='staff_of'"
    )
    return {"target_id": await _seed_role_assignment(db), "rel_type_id": rt}


_REL_EDIT = _const({"valid_from": "2020-01-01", "valid_until": "", "notes": "Empty-State note"})
_REL_INVALID_EDIT = _const({"valid_from": "2020-01-01", "valid_until": "2019-01-01"})
_REL_INVALID_CREATE = _const({"target_id": "", "rel_type_id": ""})


def _event_data(year: str, month: str = "") -> FormData:
    async def build(db, eid):
        applies_to = (
            "person"
            if await db.fetchval("SELECT 1 FROM people WHERE id=$1", eid)
            else "organization"
        )
        etype = await db.fetchval(
            "SELECT id FROM entity_event_types"
            " WHERE (applies_to = $1 OR applies_to = 'both') AND NOT requires_linked_entity"
            " ORDER BY slug LIMIT 1",
            applies_to,
        )
        return {"event_type_id": etype, "event_year": year, "event_month": month}

    return build


def _event_section(entity: str, row_prefix: str, table_id: str) -> Section:
    return Section(
        entity=entity,
        path="events",
        table_id=table_id,
        empty_text="No events",
        row_prefix=row_prefix,
        new_row_id=f"{row_prefix}-new",
        child_sql="SELECT id FROM entity_events WHERE entity_id=$1",
        create=_event_data("2001"),
        created_text="2001",
        edit=_event_data("2002"),
        edited_text="2002",
        invalid_create=_event_data("2001", "13"),
        invalid_edit=_event_data("2001", "13"),
        before_delete="archive/",
    )


_ALL_ENTITIES = ("person", "organization", "role", "role_assignment", "jurisdiction")
_IDENTIFIED = ("person", "organization", "role_assignment", "jurisdiction")  # roles carry none


def _citation_section(entity: str) -> Section:
    return Section(
        entity=entity,
        path="citations",
        table_id="citations-table-{eid}",
        tbody_id="citations-tbody-{eid}",
        empty_text="No citations yet.",
        row_prefix="citation-row",
        new_row_id="citation-row-new-{eid}",
        child_sql="SELECT id FROM citations WHERE entity_id=$1",
        create=_const({"url": "https://cite.example/empty-state"}),
        created_text="https://cite.example/empty-state",
        edit=_const({"url": "https://cite.example/empty-state", "title": "Empty-State Source"}),
        edited_text="Empty-State Source",
        invalid_create=_const({"url": "", "title": ""}),
        invalid_edit=_const({"url": "", "title": ""}),
    )


def _address_section(entity: str, empty_text: str) -> Section:
    # mode=save is the confirm modal's Keep/Accept post (no normalizer call).
    return Section(
        entity=entity,
        path="addresses",
        table_id="addresses-table",
        empty_text=empty_text,
        row_prefix="address-row",
        new_row_id="address-row-new",
        child_sql="SELECT id FROM entity_addresses WHERE entity_id=$1",
        create=_const({"address_line_1": "1 EMPTY STATE WAY", "city": "OLYMPIA", "mode": "save"}),
        created_text="1 EMPTY STATE WAY",
        edit=_const({"address_line_1": "2 EMPTY STATE WAY", "city": "OLYMPIA", "mode": "save"}),
        edited_text="2 EMPTY STATE WAY",
        invalid_create=_const({"mode": "save"}),
        invalid_edit=_const({"mode": "save"}),
    )


ONE_OFF_SECTIONS = [
    _address_section("person", "No locations"),
    _address_section("organization", "No locations"),
    _address_section("jurisdiction", "No addresses"),
    *(_citation_section(e) for e in _ALL_ENTITIES),
    _event_section("person", "person-event-row", "person-events-table"),
    _event_section("organization", "org-event-row", "org-events-table"),
    Section(
        entity="jurisdiction",
        path="relationships",
        table_id="relationships-table",
        empty_text="No relationships",
        row_prefix="relationship-row",
        new_row_id="relationship-row-new",
        child_sql="SELECT id FROM jurisdiction_relationships WHERE from_id=$1 OR to_id=$1",
        create=_jur_rel_data,
        created_text="Borders",
        edit=_REL_EDIT,
        edited_text="2020",
        invalid_create=_REL_INVALID_CREATE,
        invalid_edit=_REL_INVALID_EDIT,
    ),
    Section(
        entity="role_assignment",
        path="relationships",
        table_id="relationships-table",
        empty_text="No relationships",
        row_prefix="relationship-row",
        new_row_id="relationship-row-new",
        child_sql="SELECT id FROM role_assignment_relationships"
        " WHERE (from_assignment_id=$1 OR to_assignment_id=$1) AND archived_at IS NULL",
        create=_ra_rel_data,
        created_text="Staff Of",
        edit=_REL_EDIT,
        edited_text="2020",
        invalid_create=_REL_INVALID_CREATE,
        invalid_edit=_REL_INVALID_EDIT,
    ),
    Section(
        entity="organization",
        path="children",
        table_id="children-table",
        empty_text="No child organizations",
        row_prefix="child-row",
        new_row_id="child-row-new",
        child_sql="SELECT id FROM organizations WHERE parent_id=$1",
        create=_child_data,
        created_text="Empty-State Child Org",
    ),
    Section(
        entity="jurisdiction",
        path="affiliations",
        table_id="affiliations-table",
        empty_text="No affiliated organizations",
        row_prefix="affiliation-row",
        new_row_id="affiliation-row-new",
        child_sql="SELECT id FROM organization_jurisdiction_affiliations WHERE jurisdiction_id=$1",
        create=_jur_aff_data,
        created_text="Empty-State Affiliated Org",
        invalid_create=_const({"organization_id": "", "affiliation_type_id": ""}),
    ),
    Section(
        entity="organization",
        path="jurisdiction-affiliations",
        table_id="jur-affiliations-table",
        empty_text="No affiliated jurisdictions",
        row_prefix="jur-affiliation-row",
        new_row_id="jur-affiliation-row-new",
        child_sql="SELECT id FROM organization_jurisdiction_affiliations WHERE organization_id=$1",
        create=_org_aff_data,
        created_text="Empty-State LD",
        invalid_create=_const({"jurisdiction_id": "", "affiliation_type_id": ""}),
    ),
]


SECTIONS: list[Section] = [
    *(s for e in _ALL_ENTITIES for s in _contact_sections(e)),
    *(_link_section(e) for e in _ALL_ENTITIES),
    *(_identifier_section(e) for e in _IDENTIFIED),
    *ONE_OFF_SECTIONS,
]


def _rows_only(r) -> None:
    """A tbody re-render: read rows, not a table and not a (retargeted) form row.

    A form-row error echoes the input, so ``created_text in r.text`` alone can't
    tell success from a validation failure; no read row carries a ``<form``.
    """
    assert "HX-Retarget" not in r.headers, r.headers.get("HX-Retarget")
    assert "<tbody" not in r.text and "<table" not in r.text
    assert "<form" not in r.text


@pytest.mark.parametrize("section", SECTIONS, ids=lambda s: s.id)
async def test_tbody_round_trip(client, db, section: Section):
    seed, detail_prefix = ENTITIES[section.entity]
    eid = await seed(db)
    detail = f"{detail_prefix}/{eid}/"
    base = f"{detail_prefix}/{eid}/{section.path}"
    table_id = section.table_id.format(eid=eid)
    new_row_id = section.new_row_id.format(eid=eid)
    tbody = f"#{section.tbody_id.format(eid=eid)}" if section.tbody_id else f"#{table_id} tbody"
    target = f'hx-target="{tbody}"'

    # Empty table: the marked empty-state row, rendered by the shared partial.
    page = (await client.get(detail, headers=AUTH_HEADERS)).text
    table = table_html(page, table_id)
    assert EMPTY_MARKER in table and section.empty_text in table

    # Add the first item: the rows come back without the empty-state row.
    r = await client.post(f"{base}/", data=await section.create(db, eid), headers=HTMX_HEADERS)
    assert r.status_code == 200, r.text
    _rows_only(r)
    assert section.created_text in r.text
    assert EMPTY_MARKER not in r.text and section.empty_text not in r.text
    assert target in r.text  # the row's own delete re-renders the tbody too
    child_id = await db.fetchval(section.child_sql, eid)

    # Edit: rows again, and its form posts back into the tbody.
    if section.edit:
        form = await client.get(f"{base}/{child_id}/edit-row/", headers=HTMX_HEADERS)
        assert target in form.text
        r = await client.post(
            f"{base}/{child_id}/edit-row/", data=await section.edit(db, eid), headers=HTMX_HEADERS
        )
        assert r.status_code == 200, r.text
        _rows_only(r)
        assert section.edited_text in r.text and EMPTY_MARKER not in r.text

    # A form-row error lands on the row being edited, never into the tbody.
    if section.invalid_create:
        r = await client.post(
            f"{base}/", data=await section.invalid_create(db, eid), headers=HTMX_HEADERS
        )
        assert r.status_code == 200, r.text  # htmx swaps no 4xx
        assert r.headers.get("HX-Retarget") == f"#{new_row_id}"
        assert r.headers.get("HX-Reswap") == "outerHTML"
        assert f'id="{new_row_id}"' in r.text
    if section.invalid_edit:
        r = await client.post(
            f"{base}/{child_id}/edit-row/",
            data=await section.invalid_edit(db, eid),
            headers=HTMX_HEADERS,
        )
        row_id = f"{section.row_prefix}-{child_id}"
        assert r.status_code == 200, r.text
        assert r.headers.get("HX-Retarget") == f"#{row_id}"
        assert r.headers.get("HX-Reswap") == "outerHTML"
        assert f'id="{row_id}"' in r.text

    # The new-row form posts into the tbody.
    new_row = await client.get(
        f"{base}/new-row/", params=_new_row_params(section), headers=HTMX_HEADERS
    )
    assert target in new_row.text

    # Delete the last item: the empty-state row comes back.
    if section.before_delete:
        r = await client.post(f"{base}/{child_id}/{section.before_delete}", headers=HTMX_HEADERS)
        assert r.status_code == 200, r.text
    r = await client.delete(f"{base}/{child_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200, r.text
    _rows_only(r)
    assert EMPTY_MARKER in r.text and section.empty_text in r.text


def _new_row_params(section: Section) -> dict:
    if section.path == "contacts":
        return {"contact_type": section.new_row_id.removesuffix("-row-new")}
    return {}
