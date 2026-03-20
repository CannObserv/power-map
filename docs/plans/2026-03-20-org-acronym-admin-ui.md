# Org Acronym Admin UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add acronym create/update/delete to the org create and edit forms in the admin UI.

**Architecture:** Add an `acronym` form field to the existing `admin/orgs/form.html` template; update `org_create` and `org_update` route handlers to upsert/delete the canonical row in `organization_acronyms`; also pre-populate `canonical_acronym` context in both `org_new_form` and `org_edit_form`. No new files needed.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX, pytest

---

## Files

| Action | Path | What changes |
|--------|------|--------------|
| Modify | `src/api/admin/orgs.py` | `org_new_form`: pass `canonical_acronym=""`; `org_edit_form`: fetch + pass `canonical_acronym`; `org_create`: insert acronym row when non-empty; `org_update`: upsert/delete canonical acronym |
| Modify | `src/templates/admin/orgs/form.html` | Add `Acronym` input field between Name and Active |
| Modify | `tests/api/admin/test_orgs.py` | New tests for acronym create, acronym update (both branches), clear acronym, and edit form pre-population; patch existing test that will break |

---

### Task 1: Write failing tests

**Files:**
- Modify: `tests/api/admin/test_orgs.py`

- [ ] **Step 1: Patch the existing `test_edit_org_does_not_overwrite_acronym` test**

This test currently posts without an `acronym` key. After the handler adds `acronym: str = Form("")`, an empty string will be submitted and the new logic will delete the existing canonical acronym row — breaking this test. Fix: pass `acronym="ON"` in the POST data so the intent is preserved.

Change the `client.post` call in `test_edit_org_does_not_overwrite_acronym` (around line 205) from:
```python
        data={"name": "New Name", "active": "true", "parent_id": "", "notes": ""},
```
to:
```python
        data={"name": "New Name", "acronym": "ON", "active": "true", "parent_id": "", "notes": ""},
```

- [ ] **Step 2: Add new tests**

Append the following tests to `tests/api/admin/test_orgs.py`:

```python
def test_create_org_with_acronym_stores_acronym(client):
    """Creating an org with acronym=NEWCO must insert a canonical acronym row."""
    dsn = _get_dsn()

    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "New Company", "acronym": "NEWCO", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    location = response.headers["location"]
    # Extract org ID from redirect URL: /admin/orgs/<id>/
    created_id = location.rstrip("/").split("/")[-1]

    async def check_and_clean():
        conn = await _aconnect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                created_id,
            )
            assert row is not None and row["acronym"] == "NEWCO"
        finally:
            await conn.execute(
                "DELETE FROM organization_acronyms WHERE organization_id = $1", created_id
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", created_id
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", created_id)
            await conn.close()

    asyncio.run(check_and_clean())


def test_create_org_without_acronym_succeeds(client):
    """Creating an org with no acronym field must succeed and insert no acronym row."""
    dsn = _get_dsn()

    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "No Acronym Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    created_id = response.headers["location"].rstrip("/").split("/")[-1]

    async def check_and_clean():
        conn = await _aconnect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT id FROM organization_acronyms WHERE organization_id = $1", created_id
            )
            assert row is None, "no acronym row should be created"
        finally:
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", created_id
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", created_id)
            await conn.close()

    asyncio.run(check_and_clean())


def test_edit_org_form_shows_existing_acronym(client):
    """Edit form must pre-populate acronym field with the current canonical acronym."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical) VALUES ($1, $2, 'Acme Corp', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, 'ACME', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get(f"/admin/orgs/{oid}/edit/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert 'value="ACME"' in response.text
    finally:
        asyncio.run(teardown())


def test_edit_org_insert_new_acronym(client):
    """Posting acronym=BI when no acronym exists must insert a canonical acronym row."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical) VALUES ($1, $2, 'Beta Inc', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def get_acronym():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.post(
            f"/admin/orgs/{oid}/edit/",
            headers=AUTH_HEADERS,
            data={"name": "Beta Inc", "acronym": "BI", "active": "true", "parent_id": "", "notes": ""},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        row = asyncio.run(get_acronym())
        assert row is not None and row["acronym"] == "BI"
    finally:
        asyncio.run(teardown())


def test_edit_org_update_existing_acronym(client):
    """Posting a different acronym when one already exists must UPDATE the existing row."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical) VALUES ($1, $2, 'Gamma Corp', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, 'GC', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def get_acronym():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.post(
            f"/admin/orgs/{oid}/edit/",
            headers=AUTH_HEADERS,
            data={"name": "Gamma Corp", "acronym": "GCORP", "active": "true", "parent_id": "", "notes": ""},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        row = asyncio.run(get_acronym())
        assert row is not None and row["acronym"] == "GCORP"
    finally:
        asyncio.run(teardown())


def test_edit_org_clear_acronym(client):
    """Posting an empty acronym must delete the canonical acronym row."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical) VALUES ($1, $2, 'Delta LLC', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, 'DL', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def get_acronym():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.post(
            f"/admin/orgs/{oid}/edit/",
            headers=AUTH_HEADERS,
            data={"name": "Delta LLC", "acronym": "", "active": "true", "parent_id": "", "notes": ""},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        row = asyncio.run(get_acronym())
        assert row is None, "acronym row must be deleted when blank acronym is submitted"
    finally:
        asyncio.run(teardown())
```

- [ ] **Step 3: Run new tests — expect failures**

```bash
export $(cat env | xargs) && uv run pytest tests/api/admin/test_orgs.py \
  -k "acronym" -v --tb=short 2>&1 | tail -40
```

Expected: all 6 new tests FAIL. The patched `test_edit_org_does_not_overwrite_acronym` may PASS or FAIL — either is acceptable at this point (it will pass after handler changes).

- [ ] **Step 4: Commit tests**

```bash
git add tests/api/admin/test_orgs.py
git commit -m "#13 test: add acronym create/update/clear tests and patch existing test"
```

---

### Task 2: Implement handler and template changes

**Files:**
- Modify: `src/api/admin/orgs.py`
- Modify: `src/templates/admin/orgs/form.html`

- [ ] **Step 1: Add acronym input to form template**

In `src/templates/admin/orgs/form.html`, insert the following block after the closing `</div>` of the canonical name `form-group` (before the Active checkbox group):

```html
    <div class="form-group">
      <label for="acronym">Acronym</label>
      <input id="acronym" name="acronym" type="text" value="{{ canonical_acronym }}"
             placeholder="e.g. ACME (leave blank to remove)">
      <div class="form-group__hint">Short uppercase abbreviation. Leave blank to remove an existing acronym.</div>
    </div>
```

- [ ] **Step 2: Update `org_new_form` to pass `canonical_acronym`**

In `src/api/admin/orgs.py`, in `org_new_form`, add `"canonical_acronym": ""` to the template context dict:

```python
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": None,
            "parents": parents,
            "canonical_name": "",
            "canonical_acronym": "",
        },
    )
```

- [ ] **Step 3: Update `org_create` to accept and insert acronym**

Replace the `org_create` handler signature and body:

```python
@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(...),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
        org_id, active == "true", parent_id or None, notes or None,
    )
    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), org_id, name,
    )
    if acronym.strip():
        await db.execute(
            "INSERT INTO organization_acronyms"
            " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, acronym.strip(),
        )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
```

- [ ] **Step 4: Update `org_edit_form` to fetch and pass `canonical_acronym`**

After the existing `canonical` fetch in `org_edit_form`, add:

```python
    canonical_acronym_row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
```

Then update the template context dict to include:

```python
            "canonical_acronym": canonical_acronym_row["acronym"] if canonical_acronym_row else "",
```

- [ ] **Step 5: Update `org_update` to accept and upsert/delete acronym**

Replace the `org_update` handler signature and body:

```python
@router.post("/{org_id}/edit/")
async def org_update(
    org_id: str,
    request: Request,
    name: str = Form(...),
    acronym: str = Form(""),
    active: str = Form(""),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.execute(
        "UPDATE organizations SET active = $1, parent_id = $2, notes = $3 WHERE id = $4",
        active == "true", parent_id or None, notes or None, org_id,
    )
    existing = await db.fetchrow(
        "SELECT id FROM organization_names"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    if existing:
        await db.execute(
            "UPDATE organization_names SET name = $1 WHERE id = $2", name, existing["id"]
        )
    else:
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, name,
        )
    acronym_stripped = acronym.strip()
    existing_acronym = await db.fetchrow(
        "SELECT id FROM organization_acronyms"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    if acronym_stripped:
        if existing_acronym:
            await db.execute(
                "UPDATE organization_acronyms SET acronym = $1 WHERE id = $2",
                acronym_stripped, existing_acronym["id"],
            )
        else:
            await db.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical) VALUES ($1, $2, $3, TRUE)",
                generate_id(), org_id, acronym_stripped,
            )
    else:
        if existing_acronym:
            await db.execute(
                "DELETE FROM organization_acronyms WHERE id = $1", existing_acronym["id"]
            )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
```

- [ ] **Step 6: Run all acronym tests — expect all pass**

```bash
export $(cat env | xargs) && uv run pytest tests/api/admin/test_orgs.py \
  -k "acronym" -v --tb=short 2>&1 | tail -40
```

Expected: all 6 new tests + `test_edit_org_does_not_overwrite_acronym` PASS.

- [ ] **Step 7: Run full orgs test suite — no regressions**

```bash
export $(cat env | xargs) && uv run pytest tests/api/admin/test_orgs.py -v --tb=short 2>&1 | tail -40
```

Expected: all tests PASS.

- [ ] **Step 8: Lint**

```bash
uv run ruff check src/api/admin/orgs.py src/templates/admin/orgs/form.html tests/api/admin/test_orgs.py
```

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin/orgs.py src/templates/admin/orgs/form.html
git commit -m "#13 feat: upsert/delete canonical acronym in org create and edit handlers"
```
