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
    edit: FormData
    edited_text: str
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


_ALL_ENTITIES = ("person", "organization", "role", "role_assignment", "jurisdiction")

SECTIONS: list[Section] = [
    *(s for e in _ALL_ENTITIES for s in _contact_sections(e)),
    *(_link_section(e) for e in _ALL_ENTITIES),
    *(
        _identifier_section(e)
        for e in ("person", "organization", "role_assignment", "jurisdiction")
    ),
]


def _rows_only(html: str) -> None:
    """A tbody re-render: rows, not a table or a bare form row."""
    assert "<tbody" not in html and "<table" not in html


@pytest.mark.parametrize("section", SECTIONS, ids=lambda s: s.id)
async def test_tbody_round_trip(client, db, section: Section):
    seed, detail_prefix = ENTITIES[section.entity]
    eid = await seed(db)
    detail = f"{detail_prefix}/{eid}/"
    base = f"{detail_prefix}/{eid}/{section.path}"
    target = f'hx-target="#{section.table_id} tbody"'

    # Empty table: the marked empty-state row, rendered by the shared partial.
    page = (await client.get(detail, headers=AUTH_HEADERS)).text
    table = table_html(page, section.table_id)
    assert EMPTY_MARKER in table and section.empty_text in table

    # Add the first item: the rows come back without the empty-state row.
    r = await client.post(f"{base}/", data=await section.create(db, eid), headers=HTMX_HEADERS)
    assert r.status_code == 200, r.text
    _rows_only(r.text)
    assert section.created_text in r.text
    assert EMPTY_MARKER not in r.text and section.empty_text not in r.text
    assert target in r.text  # the row's own delete re-renders the tbody too
    child_id = await db.fetchval(section.child_sql, eid)

    # Edit: rows again, and its form posts back into the tbody.
    form = await client.get(f"{base}/{child_id}/edit-row/", headers=HTMX_HEADERS)
    assert target in form.text
    r = await client.post(
        f"{base}/{child_id}/edit-row/", data=await section.edit(db, eid), headers=HTMX_HEADERS
    )
    assert r.status_code == 200, r.text
    _rows_only(r.text)
    assert section.edited_text in r.text and EMPTY_MARKER not in r.text

    # A form-row error lands on the row being edited, never into the tbody.
    if section.invalid_create:
        r = await client.post(
            f"{base}/", data=await section.invalid_create(db, eid), headers=HTMX_HEADERS
        )
        assert r.headers.get("HX-Retarget") == f"#{section.new_row_id}"
        assert r.headers.get("HX-Reswap") == "outerHTML"
        assert f'id="{section.new_row_id}"' in r.text
    if section.invalid_edit:
        r = await client.post(
            f"{base}/{child_id}/edit-row/",
            data=await section.invalid_edit(db, eid),
            headers=HTMX_HEADERS,
        )
        row_id = f"{section.row_prefix}-{child_id}"
        assert r.headers.get("HX-Retarget") == f"#{row_id}"
        assert r.headers.get("HX-Reswap") == "outerHTML"
        assert f'id="{row_id}"' in r.text

    # The new-row form posts into the tbody.
    new_row = await client.get(
        f"{base}/new-row/", params=_new_row_params(section), headers=HTMX_HEADERS
    )
    assert target in new_row.text

    # Delete the last item: the empty-state row comes back.
    r = await client.delete(f"{base}/{child_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200, r.text
    _rows_only(r.text)
    assert EMPTY_MARKER in r.text and section.empty_text in r.text


def _new_row_params(section: Section) -> dict:
    if section.path == "contacts":
        return {"contact_type": section.new_row_id.removesuffix("-row-new")}
    return {}
