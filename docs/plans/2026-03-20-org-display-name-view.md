# Org Display Name View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display organization names as "Name (Acronym)" throughout the admin UI by adding a `v_org_display_names` view and updating all queries that join `organization_names` for display.

**Architecture:** Add `CREATE OR REPLACE VIEW v_org_display_names` to `schema.sql`. Each of the ~15 query sites in `orgs.py`, `roles.py`, `role_assignments.py`, and `people.py` swaps `LEFT JOIN organization_names n ON ... AND n.is_canonical = TRUE` for `LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id` and renames `n.name` references to `dn.display_name`. Templates are unchanged — variable names `org_name` and `canonical_name` stay the same.

**Tech Stack:** PostgreSQL (view), asyncpg, FastAPI, pytest (integration tests require `DATABASE_URL`)

---

## File Map

| File | Change |
|---|---|
| `src/core/schema.sql` | Add `CREATE OR REPLACE VIEW v_org_display_names` |
| `src/api/admin/orgs.py` | Update 5 query sites |
| `src/api/admin/roles.py` | Update 5 query sites |
| `src/api/admin/role_assignments.py` | Update `_LIST_SELECT`, `_fetch_roles`, `ra_list` count, `ra_detail` |
| `src/api/admin/people.py` | Update `person_detail` role_assignments query |
| `tests/core/test_schema_view.py` | New: integration tests for the view |
| `tests/api/admin/test_orgs.py` | Add: no-duplicate test for org with acronym |
| `tests/api/admin/test_role_assignments.py` | Add: display name format test |

---

### Task 1: Add `v_org_display_names` to schema and test it

**Files:**
- Modify: `src/core/schema.sql`
- Create: `tests/core/test_schema_view.py`

The view definition:
```sql
CREATE OR REPLACE VIEW v_org_display_names AS
SELECT o.id AS organization_id,
       COALESCE(nl.name || ' (' || na.name || ')', nl.name) AS display_name
FROM organizations o
LEFT JOIN organization_names nl
    ON nl.organization_id = o.id AND nl.is_canonical = TRUE AND nl.name_type != 'acronym'
LEFT JOIN organization_names na
    ON na.organization_id = o.id AND na.is_canonical = TRUE AND na.name_type = 'acronym'
```

- [ ] **Step 1.1: Write failing integration tests**

Create `tests/core/test_schema_view.py`:

```python
"""Integration tests for v_org_display_names view."""

import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    c = await asyncpg.connect(dsn)
    await apply_schema(c)
    yield c
    await c.close()


async def _insert_org(conn, name, acronym=None):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(), oid, name,
    )
    if acronym:
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, 'acronym', TRUE)",
            generate_id(), oid, acronym,
        )
    return oid


async def test_view_formats_name_with_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert row["display_name"] == "National Cannabis Industry Association (NCIA)"
    finally:
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)


async def test_view_shows_name_only_when_no_acronym(conn):
    oid = await _insert_org(conn, "Small Local Org")
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert row["display_name"] == "Small Local Org"
    finally:
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)


async def test_view_returns_one_row_per_org_with_both_name_and_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    try:
        rows = await conn.fetch(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert len(rows) == 1
    finally:
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
cd .worktrees/feat/org-display-name-view
uv run pytest tests/core/test_schema_view.py -v
```

Expected: FAIL — view does not exist yet.

- [ ] **Step 1.3: Add the view to `schema.sql`**

Insert after the `uq_org_canonical_name` index (after line 97), before the `people` table:

```sql
-- Display name view: formats org name as "Name (Acronym)" when acronym is present.
-- Used by all admin queries that show an org name for display (not editing).
CREATE OR REPLACE VIEW v_org_display_names AS
SELECT o.id AS organization_id,
       COALESCE(nl.name || ' (' || na.name || ')', nl.name) AS display_name
FROM organizations o
LEFT JOIN organization_names nl
    ON nl.organization_id = o.id AND nl.is_canonical = TRUE AND nl.name_type != 'acronym'
LEFT JOIN organization_names na
    ON na.organization_id = o.id AND na.is_canonical = TRUE AND na.name_type = 'acronym'
;
```

- [ ] **Step 1.4: Run tests to confirm they pass**

```bash
uv run pytest tests/core/test_schema_view.py -v
```

Expected: 3 passed.

- [ ] **Step 1.5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_view.py
git commit -m "#11 feat: add v_org_display_names view to schema"
```

---

### Task 2: Update `orgs.py`

**Files:**
- Modify: `src/api/admin/orgs.py`
- Modify: `tests/api/admin/test_orgs.py`

Five query sites to update:

| Location | Current alias | Change |
|---|---|---|
| `orgs_list` count (line ~53) | `n` | → `dn`; `n.name ILIKE $q` → `dn.display_name ILIKE $q` |
| `orgs_list` rows (line ~62) | `n` | → `dn`; `n.name AS canonical_name` → `dn.display_name AS canonical_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `org_new_form` parents (line ~101) | `n` | → `dn`; `n.name AS canonical_name` → `dn.display_name AS canonical_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `org_detail` children (line ~197) | `n` | → `dn`; `n.name AS canonical_name` → `dn.display_name AS canonical_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `org_edit_form` parents (line ~246) | `n` | → `dn`; `n.name AS canonical_name` → `dn.display_name AS canonical_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |

**Note:** The `org_edit_form` query at line 241 (`SELECT name FROM organization_names WHERE organization_id = $1 AND is_canonical = TRUE`) fetches the name for the edit input field — this is not a display query and must NOT be changed.

- [ ] **Step 2.1: Add a failing test to `tests/api/admin/test_orgs.py`**

`test_orgs.py` uses `asyncio.run(setup())` (not async fixtures) — stay consistent. Append:

```python
def test_org_with_acronym_appears_once_in_list_with_formatted_name(client):
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Cannabis Alliance', 'legal', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'CA', 'acronym', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.text.count(oid) == 1, "org must appear exactly once"
        assert "Cannabis Alliance (CA)" in response.text
    finally:
        asyncio.run(teardown())
```

- [ ] **Step 2.2: Run the test to confirm it fails**

```bash
uv run pytest tests/api/admin/test_orgs.py::test_org_with_acronym_appears_once_in_list_with_formatted_name -v
```

Expected: FAIL — list shows two rows and plain names, not formatted.

- [ ] **Step 2.3: Update the 5 query sites in `orgs.py`**

**orgs_list count query** — replace:
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT o.id)
            FROM organizations o
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}""",
```
with:
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT o.id)
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
```

**orgs_list where condition** — the search condition `"n.name ILIKE ${len(params)}"` → `"dn.display_name ILIKE ${len(params)}"`.

**orgs_list rows query** — replace:
```python
    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at, o.created_at,
                   n.name AS canonical_name
            FROM organizations o
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
```
with:
```python
    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at, o.created_at,
                   dn.display_name AS canonical_name
            FROM organizations o
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}
            ORDER BY dn.display_name NULLS LAST
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
```

**org_new_form parents query** — replace:
```python
    parents = await db.fetch(
        """SELECT o.id, n.name AS canonical_name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL ORDER BY n.name NULLS LAST"""
    )
```
with:
```python
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )
```

**org_detail children query** — replace:
```python
    children = await db.fetch(
        """SELECT o.id, o.active, o.archived_at, n.name AS canonical_name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.parent_id = $1 ORDER BY n.name""",
```
with:
```python
    children = await db.fetch(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.parent_id = $1 ORDER BY dn.display_name""",
```

**org_edit_form parents query** — replace:
```python
    parents = await db.fetch(
        """SELECT o.id, n.name AS canonical_name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL AND o.id != $1 ORDER BY n.name NULLS LAST""",
```
with:
```python
    parents = await db.fetch(
        """SELECT o.id, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL AND o.id != $1 ORDER BY dn.display_name NULLS LAST""",
```

- [ ] **Step 2.4: Run orgs tests**

```bash
uv run pytest tests/api/admin/test_orgs.py -v
```

Expected: all pass including the new test.

- [ ] **Step 2.5: Commit**

```bash
git add src/api/admin/orgs.py tests/api/admin/test_orgs.py
git commit -m "#11 feat: use v_org_display_names in orgs.py"
```

---

### Task 3: Update `roles.py`

**Files:**
- Modify: `src/api/admin/roles.py`

Five query sites:

| Location | Change |
|---|---|
| `roles_list` count (line ~67) | `n` → `dn`; `n.name ILIKE $org_q` → `dn.display_name ILIKE $org_q` |
| `roles_list` rows (line ~78) | `n` → `dn`; `n.name AS org_name` → `dn.display_name AS org_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `role_new_form` orgs (line ~118) | `n` → `dn`; `n.name` (unaliased) → `dn.display_name AS name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `role_detail` (line ~172) | `n` → `dn`; `n.name AS org_name` → `dn.display_name AS org_name` |
| `role_edit_form` orgs (line ~222) | same as `role_new_form` |

**Note on `role_new_form` and `role_edit_form`:** These queries select `n.name` (no alias), and the template uses `{{ org.name or org.id }}`. Change to `dn.display_name AS name` so the template field name stays `name`.

- [ ] **Step 3.1: Update all 5 query sites in `roles.py`**

**roles_list where condition**: `"n.name ILIKE ${len(params)} ESCAPE '\\\\'"` → `"dn.display_name ILIKE ${len(params)} ESCAPE '\\\\'"`

**roles_list count query** — replace:
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT r.id)
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}""",
```
with:
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT r.id)
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
```

**roles_list rows query** — replace:
```python
    rows = await db.fetch(
        f"""SELECT r.id, r.title, r.notes, r.archived_at, r.created_at,
                   o.id AS org_id,
                   n.name AS org_name
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST, r.title
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
```
with:
```python
    rows = await db.fetch(
        f"""SELECT r.id, r.title, r.notes, r.archived_at, r.created_at,
                   o.id AS org_id,
                   dn.display_name AS org_name
            FROM roles r
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}
            ORDER BY dn.display_name NULLS LAST, r.title
            LIMIT ${len(list_params) - 1} OFFSET ${len(list_params)}""",
```

**role_new_form orgs query** — replace:
```python
    orgs = await db.fetch(
        """SELECT o.id, n.name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL ORDER BY n.name NULLS LAST"""
    )
```
with:
```python
    orgs = await db.fetch(
        """SELECT o.id, dn.display_name AS name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )
```

**role_detail query** — replace:
```python
    role = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  o.id AS org_id, n.name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE r.id = $1""",
```
with:
```python
    role = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  o.id AS org_id, dn.display_name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE r.id = $1""",
```

**role_edit_form orgs query** — same as `role_new_form`:
```python
    orgs = await db.fetch(
        """SELECT o.id, dn.display_name AS name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE o.archived_at IS NULL ORDER BY dn.display_name NULLS LAST"""
    )
```

- [ ] **Step 3.2: Run roles tests**

```bash
uv run pytest tests/api/admin/test_roles.py tests/api/admin/test_roles_helpers.py -v
```

Expected: all pass.

- [ ] **Step 3.3: Commit**

```bash
git add src/api/admin/roles.py
git commit -m "#11 feat: use v_org_display_names in roles.py"
```

---

### Task 4: Update `role_assignments.py`

**Files:**
- Modify: `src/api/admin/role_assignments.py`
- Modify: `tests/api/admin/test_role_assignments.py`

Four locations:

| Location | Change |
|---|---|
| `_fetch_roles` (line ~43) | `n` → `dn`; `n.name AS org_name` → `dn.display_name AS org_name`; `ORDER BY n.name` → `ORDER BY dn.display_name` |
| `_LIST_SELECT` (line ~61) | `n` → `dn`; `n.name AS org_name` → `dn.display_name AS org_name` |
| `ra_list` count query (line ~108) | `n` → `dn` in the FROM section |
| `ra_list` search condition (line ~94) | `n.name ILIKE ${idx}` → `dn.display_name ILIKE ${idx}` |
| `ra_detail` query (line ~245) | `n` → `dn`; `n.name AS org_name` → `dn.display_name AS org_name` |

**Critical:** `_LIST_SELECT` is a module-level constant whose alias `n` is also referenced in the `ra_list` WHERE condition. After renaming to `dn`, update both `_LIST_SELECT` and the condition string in `ra_list`.

- [ ] **Step 4.1: Add a failing test to `test_role_assignments.py`**

The `db`, `client`, `org_id`, `person_id`, and `role_id` fixtures are already defined in this file. The test validates that both `_LIST_SELECT` (SELECT clause) and the `ra_list` search condition (WHERE clause) use the correct `dn.display_name` alias after the refactor. Append:

```python
async def test_ra_list_shows_formatted_org_name_for_org_with_acronym(client, db, org_id, person_id, role_id):
    """Role assignment list should show 'Name (Acronym)' and no duplicate rows."""
    # Add acronym to the test org
    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'TO', 'acronym', TRUE)",
        generate_id(), org_id,
    )
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, FALSE)",
        ra_id, person_id, role_id,
    )
    try:
        response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.text.count(ra_id) == 1, "assignment must appear exactly once"
        assert "Test Org (TO)" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)
        await db.execute(
            "DELETE FROM organization_names WHERE organization_id = $1 AND name_type = 'acronym'",
            org_id,
        )
```

- [ ] **Step 4.2: Run to confirm failure**

```bash
uv run pytest tests/api/admin/test_role_assignments.py::test_ra_list_shows_formatted_org_name_for_org_with_acronym -v
```

Expected: FAIL.

- [ ] **Step 4.3: Update `_fetch_roles`**

Replace:
```python
async def _fetch_roles(db):
    """Fetch active roles for select options."""
    return await db.fetch(
        """SELECT r.id, r.title, n.name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE r.archived_at IS NULL
           ORDER BY n.name NULLS LAST, r.title"""
    )
```
with:
```python
async def _fetch_roles(db):
    """Fetch active roles for select options."""
    return await db.fetch(
        """SELECT r.id, r.title, dn.display_name AS org_name
           FROM roles r
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE r.archived_at IS NULL
           ORDER BY dn.display_name NULLS LAST, r.title"""
    )
```

- [ ] **Step 4.4: Update `_LIST_SELECT`**

Replace:
```python
_LIST_SELECT = """
    SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.created_at,
           p.id AS person_id,
           pn.name AS person_name,
           r.id AS role_id, r.title AS role_title,
           o.id AS org_id,
           n.name AS org_name
    FROM role_assignments ra
    JOIN people p ON p.id = ra.person_id
    LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
    JOIN roles r ON r.id = ra.role_id
    JOIN organizations o ON o.id = r.organization_id
    LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
"""
```
with:
```python
_LIST_SELECT = """
    SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at, ra.created_at,
           p.id AS person_id,
           pn.name AS person_name,
           r.id AS role_id, r.title AS role_title,
           o.id AS org_id,
           dn.display_name AS org_name
    FROM role_assignments ra
    JOIN people p ON p.id = ra.person_id
    LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
    JOIN roles r ON r.id = ra.role_id
    JOIN organizations o ON o.id = r.organization_id
    LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
"""
```

- [ ] **Step 4.5: Update `ra_list` search condition and count query**

In `ra_list`, the search condition references `n.name`:
```python
        conditions.append(
            f"(pn.name ILIKE ${idx} OR r.title ILIKE ${idx} OR n.name ILIKE ${idx})"
        )
```
Change `n.name ILIKE ${idx}` → `dn.display_name ILIKE ${idx}`:
```python
        conditions.append(
            f"(pn.name ILIKE ${idx} OR r.title ILIKE ${idx} OR dn.display_name ILIKE ${idx})"
        )
```

Also update the count query's FROM section:
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT ra.id)
            FROM role_assignments ra
            JOIN people p ON p.id = ra.person_id
            LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
            JOIN roles r ON r.id = ra.role_id
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}""",
```
→
```python
    count = await db.fetchval(
        f"""SELECT count(DISTINCT ra.id)
            FROM role_assignments ra
            JOIN people p ON p.id = ra.person_id
            LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
            JOIN roles r ON r.id = ra.role_id
            JOIN organizations o ON o.id = r.organization_id
            LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
            {where}""",
```

- [ ] **Step 4.6: Update `ra_detail` query**

Replace:
```python
    ra = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  ra.created_at, ra.notes,
                  p.id AS person_id,
                  pn.name AS person_name,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  n.name AS org_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE ra.id = $1""",
```
with:
```python
    ra = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  ra.created_at, ra.notes,
                  p.id AS person_id,
                  pn.name AS person_name,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.id = $1""",
```

- [ ] **Step 4.7: Run role_assignments tests**

```bash
uv run pytest tests/api/admin/test_role_assignments.py -v
```

Expected: all pass.

- [ ] **Step 4.8: Commit**

```bash
git add src/api/admin/role_assignments.py tests/api/admin/test_role_assignments.py
git commit -m "#11 feat: use v_org_display_names in role_assignments.py"
```

---

### Task 5: Update `people.py`

**Files:**
- Modify: `src/api/admin/people.py`

One query site: `person_detail` role_assignments fetch.

- [ ] **Step 5.1: Update the query in `person_detail`**

Replace:
```python
    role_assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.title, o.id AS org_id, n.name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
```
with:
```python
    role_assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.title, o.id AS org_id, dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
```

- [ ] **Step 5.2: Run people tests**

```bash
uv run pytest tests/api/admin/test_people.py -v
```

Expected: all pass.

- [ ] **Step 5.3: Commit**

```bash
git add src/api/admin/people.py
git commit -m "#11 feat: use v_org_display_names in people.py"
```

---

### Task 6: Full test suite

- [ ] **Step 6.1: Run full suite**

```bash
uv run pytest -v
```

Expected: all tests pass (integration tests skipped if no DATABASE_URL).

- [ ] **Step 6.2: Run linter**

```bash
uv run ruff check .
```

Expected: no issues.
