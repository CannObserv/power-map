# Org Detail UI — Round 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply #28 UX patterns to remaining org detail sections: restructure Hierarchy into an entity-card, consolidate Addresses + Contact Methods into a new Contact Information entity-card (Email / Phone / Locations subsections), apply form-group / pill-toggle / button-group consistency to all remaining form rows, and move `+ Add` buttons into subsection header rows for Links and Identifiers.

**Architecture:** All changes are template-only except two backend touchpoints — the `contact_new_row` route gains a required `contact_type` query param, and the `contact_edit_row_post` route drops `contact_type` from its UPDATE (type is immutable after creation). The `org_detail` handler splits the single `contacts` fetch into `email_contacts` + `phone_contacts`. No schema changes.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX, pytest integration tests (`-m integration`, requires `TEST_DATABASE_URL`)

---

## File Map

| File | Change |
|---|---|
| `src/api/admin/orgs_contacts.py` | `new_row`: add `contact_type: str = Query(...)` param; `edit_row_post`: remove `contact_type` from form + SQL |
| `src/api/admin/orgs.py` | `org_detail`: replace single `contacts` fetch with `email_contacts` + `phone_contacts` |
| `src/templates/admin/orgs/detail.html` | Hierarchy card, Contact Information section, Links/Identifiers header-row buttons |
| `src/templates/admin/orgs/partials/_contact_form_row.html` | Remove type select; new=hidden field, edit=readonly label; form-group + button group |
| `src/templates/admin/orgs/partials/_contact_row.html` | Remove type `<td>` (columns: Value, Label, Actions) |
| `src/templates/admin/orgs/partials/_address_form_row.html` | form-group wrappers + button group |
| `src/templates/admin/orgs/partials/_link_form_row.html` | form-group wrappers + pill toggles for is_active/is_canonical + button group |
| `src/templates/admin/orgs/partials/_identifier_form_row.html` | form-group wrappers + button group |
| `src/templates/admin/orgs/partials/_child_form_row.html` | form-group wrapper on search input |
| `tests/api/admin/test_orgs_contacts.py` | Add new-row tests for contact_type param; update existing test for immutable type |

---

## Task 1: `orgs_contacts.py` — `contact_type` query param on new-row; immutable on edit

**Files:**
- Modify: `src/api/admin/orgs_contacts.py`
- Modify: `tests/api/admin/test_orgs_contacts.py`

- [ ] **Step 1: Write the failing tests**

Also update the existing `test_contacts_new_row_returns_form` to pass `contact_type` — it will get a 422 once the param becomes required:

```python
def test_contacts_new_row_returns_form(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "<form" in r.text
```

Add new tests:

```python
def test_contacts_new_row_email_has_hidden_type(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="email"' in r.text
    assert "email-row-new" in r.text


def test_contacts_new_row_phone_has_hidden_type(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=phone",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'type="hidden"' in r.text
    assert 'value="phone"' in r.text
    assert "phone-row-new" in r.text


def test_contacts_update_does_not_change_type(client, org_and_contact):
    """contact_type is immutable — edit route ignores any type in POST data."""
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "+12065559876"},  # no contact_type submitted
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/29-org-detail-ui-round3
uv run pytest tests/api/admin/test_orgs_contacts.py::test_contacts_new_row_email_has_hidden_type tests/api/admin/test_orgs_contacts.py::test_contacts_new_row_phone_has_hidden_type tests/api/admin/test_orgs_contacts.py::test_contacts_update_does_not_change_type -v -m integration
```

Expected: FAIL (422 on missing contact_type, missing hidden field, and current edit route requires contact_type in form)

- [ ] **Step 3: Update `orgs_contacts.py`**

Add `Query` to imports:

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
```

Replace `contact_new_row`:

```python
@router.get("/new-row/")
async def contact_new_row(
    org_id: str,
    request: Request,
    contact_type: str = Query(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return empty contact form row for the given contact_type (email|phone)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_form_row.html",
        {"org_id": org_id, "c": None, "contact_type": contact_type},
    )
```

Replace `contact_edit_row_post` — remove `contact_type` param and drop it from the UPDATE:

```python
@router.post("/{contact_id}/edit-row/")
async def contact_edit_row_post(
    org_id: str,
    contact_id: str,
    request: Request,
    value: str = Form(...),
    display_label: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Update an organization contact method (contact_type is immutable)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE contact_methods SET value=$1, display_label=$2 WHERE id=$3",
        value.strip(),
        display_label.strip() or None,
        contact_id,
    )
    row = await db.fetchrow("SELECT * FROM contact_methods WHERE id=$1", contact_id)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_contact_row.html", {"org_id": org_id, "c": row}
    )
```

Also update `contact_edit_row_get` to pass `contact_type` from the existing record:

```python
@router.get("/{contact_id}/edit-row/")
async def contact_edit_row_get(
    org_id: str,
    contact_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return contact edit form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    contact = await db.fetchrow(
        "SELECT * FROM contact_methods"
        " WHERE id=$1 AND entity_type='organization' AND entity_id=$2",
        contact_id,
        org_id,
    )
    if not contact:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_form_row.html",
        {"org_id": org_id, "c": contact, "contact_type": contact["contact_type"]},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/api/admin/test_orgs_contacts.py -v -m integration
```

Expected: all pass. The existing `test_contacts_update` still passes — FastAPI ignores the extra `contact_type` field it no longer declares, and the test data for value is present.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/orgs_contacts.py tests/api/admin/test_orgs_contacts.py
git commit -m "#29 feat: contact_type query param on new-row; immutable on edit"
```

---

## Task 2: Contact templates — form row and read row

**Files:**
- Modify: `src/templates/admin/orgs/partials/_contact_form_row.html`
- Modify: `src/templates/admin/orgs/partials/_contact_row.html`

The read row drops the Type column (rows live in typed tables). The form row uses a hidden field for new rows and a muted label for edit rows. Both get form-group wrappers and the standard button group.

- [ ] **Step 1: Write failing test**

Add to `tests/api/admin/test_orgs_contacts.py`:

```python
def test_contacts_form_row_has_form_group(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(
        f"/admin/orgs/{oid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert "form-group" in r.text


def test_contacts_edit_row_no_type_select(client, org_and_contact):
    """Edit form must not contain a contact_type <select> (type is immutable)."""
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="contact_type"' not in r.text or '<select' not in r.text


def test_contacts_read_row_no_type_cell(client, org_and_contact):
    """Read row must not render a standalone type cell (rows live in typed tables)."""
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    # contact_type value should not appear as a standalone column
    assert "<td>phone</td>" not in r.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs_contacts.py::test_contacts_form_row_has_form_group tests/api/admin/test_orgs_contacts.py::test_contacts_edit_row_no_type_select tests/api/admin/test_orgs_contacts.py::test_contacts_read_row_no_type_cell -v -m integration
```

- [ ] **Step 3: Rewrite `_contact_form_row.html`**

```jinja
{# admin/orgs/partials/_contact_form_row.html #}
<tr id="{% if c %}contact-row-{{ c.id }}{% else %}{{ contact_type }}-row-new{% endif %}">
  <td colspan="3" style="padding:var(--space-2) var(--space-4)">
    <form {% if c %}
          hx-post="/admin/orgs/{{ org_id }}/contacts/{{ c.id }}/edit-row/"
          hx-target="#contact-row-{{ c.id }}"
          hx-swap="outerHTML"
          {% else %}
          hx-post="/admin/orgs/{{ org_id }}/contacts/"
          hx-target="#{{ contact_type }}-row-new"
          hx-swap="outerHTML"
          {% endif %}
          style="display:flex;gap:var(--space-2);align-items:center">
      {% if c %}
      <span style="font-size:var(--font-size-sm);color:var(--color-text-muted);white-space:nowrap">{{ c.contact_type }}</span>
      {% else %}
      <input type="hidden" name="contact_type" value="{{ contact_type }}">
      {% endif %}
      <div class="form-group" style="margin-bottom:0;flex:1">
        <input type="text" name="value" required
               value="{{ c.value if c else '' }}"
               placeholder="{{ 'Email address' if contact_type == 'email' else 'Phone number' }}">
      </div>
      <div class="form-group" style="margin-bottom:0">
        <input type="text" name="display_label"
               value="{{ c.display_label or '' if c else '' }}" placeholder="Label (optional)">
      </div>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if c %}
                hx-get="/admin/orgs/{{ org_id }}/contacts/{{ c.id }}/read-row/"
                hx-target="#contact-row-{{ c.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 4: Rewrite `_contact_row.html`**

Columns: Value, Label, Actions (3 cols — no Type column):

```jinja
{# admin/orgs/partials/_contact_row.html #}
<tr id="contact-row-{{ c.id }}">
  <td>{{ c.value }}</td>
  <td>{{ c.display_label or '—' }}</td>
  <td>
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/orgs/{{ org_id }}/contacts/{{ c.id }}/edit-row/"
            hx-target="#contact-row-{{ c.id }}"
            hx-swap="outerHTML">Edit</button>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/orgs/{{ org_id }}/contacts/{{ c.id }}/"
            hx-target="#contact-row-{{ c.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete this contact?">Delete</button>
  </td>
</tr>
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs_contacts.py -v -m integration
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/orgs/partials/_contact_form_row.html \
        src/templates/admin/orgs/partials/_contact_row.html \
        tests/api/admin/test_orgs_contacts.py
git commit -m "#29 feat: contact form/read rows — typed hidden field, form-group, no type column"
```

---

## Task 3: `orgs.py` — split contacts fetch into `email_contacts` + `phone_contacts`

**Files:**
- Modify: `src/api/admin/orgs.py`
- Modify: `tests/api/admin/test_orgs.py`

- [ ] **Step 1: Write the failing test**

Find the existing detail test in `tests/api/admin/test_orgs.py` and add:

```python
def test_org_detail_has_email_and_phone_tables(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/admin/test_orgs.py::test_org_detail_has_email_and_phone_tables -v -m integration
```

Expected: FAIL (tables not yet present in template)

- [ ] **Step 3: Update `org_detail` in `orgs.py`**

Replace the single `contacts` fetch (around line 661):

```python
    email_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'email'",
        org_id,
    )
    phone_contacts = await db.fetch(
        "SELECT * FROM contact_methods"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND contact_type = 'phone'",
        org_id,
    )
```

Update the `TemplateResponse` context — replace `"contacts": contacts` with:

```python
"email_contacts": email_contacts,
"phone_contacts": phone_contacts,
```

- [ ] **Step 4: Run full test suite (non-integration) to check for import errors**

```bash
uv run pytest -q --tb=short --ignore=tests/api/admin
```

Expected: pass (template changes come in Task 5 — this just verifies no Python errors)

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/orgs.py tests/api/admin/test_orgs.py
git commit -m "#29 feat: split contacts fetch into email_contacts + phone_contacts"
```

---

## Task 4: `detail.html` — Hierarchy section into entity-card

**Files:**
- Modify: `src/templates/admin/orgs/detail.html`

- [ ] **Step 1: Write failing test**

Add to `tests/api/admin/test_orgs.py`:

```python
def test_org_detail_hierarchy_has_entity_card(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    # Both subsection labels should appear inside the hierarchy section
    assert "Parent Organization" in r.text
    assert "Child Organizations" in r.text
    # field-group-label must be used (not bare <h3>)
    assert "field-group-label" in r.text
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/admin/test_orgs.py::test_org_detail_hierarchy_has_entity_card -v -m integration
```

Expected: FAIL (current template uses bare `<h3>` and no entity-card)

- [ ] **Step 3: Replace the Hierarchy section in `detail.html`**

Replace lines 69–92 (the `<section id="section-hierarchy">` block) with:

```jinja
<section id="section-hierarchy" class="entity-section">
  <h2>Hierarchy</h2>
  <div class="entity-card">

    <div style="display:flex;align-items:center;justify-content:space-between;margin:0 0 var(--space-3)">
      <h3 class="field-group-label">Parent Organization</h3>
    </div>
    {% include "admin/orgs/partials/_parent_read.html" %}

    <div style="display:flex;align-items:center;justify-content:space-between;margin:var(--space-5) 0 var(--space-3)">
      <h3 class="field-group-label">Child Organizations</h3>
      <button class="btn btn--sm btn--secondary"
              hx-get="/admin/orgs/{{ org.id }}/children/new-row/"
              hx-target="#children-table tbody"
              hx-swap="afterbegin"
              type="button">+ Add child</button>
    </div>
    <div class="table-wrapper">
      <table id="children-table" class="data-table">
        <thead><tr><th scope="col">Name</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
        <tbody>
          {% for child in children %}
          {% include "admin/orgs/partials/_child_row.html" %}
          {% else %}
          <tr><td colspan="3" style="text-align:center;color:var(--color-text-muted)">No child organizations</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

  </div>
</section>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs.py -v -m integration
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/detail.html tests/api/admin/test_orgs.py
git commit -m "#29 feat: Hierarchy section — entity-card with Parent + Children subsections"
```

---

## Task 5: `detail.html` — Contact Information section (replaces Addresses + Contact Methods)

**Files:**
- Modify: `src/templates/admin/orgs/detail.html`

- [ ] **Step 1: Write failing tests**

Add to `tests/api/admin/test_orgs.py`:

```python
def test_org_detail_contact_information_section(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Contact Information" in r.text
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text
    assert 'id="addresses-table"' in r.text
    # Old standalone sections should be gone
    assert "<h2>Addresses</h2>" not in r.text
    assert "<h2>Contact Methods</h2>" not in r.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs.py::test_org_detail_contact_information_section -v -m integration
```

- [ ] **Step 3: Replace Addresses + Contact Methods sections; add Contact Information**

In `detail.html`, replace the standalone Addresses section (starts at `<section class="entity-section">` containing `<h2>Addresses</h2>`) and the standalone Contact Methods section (containing `<h2>Contact Methods</h2>`) with the following single section. Place it in the same position in the page order (after Hierarchy, before Links).

```jinja
<section class="entity-section">
  <h2>Contact Information</h2>
  <div class="entity-card">

    <div style="display:flex;align-items:center;justify-content:space-between;margin:0 0 var(--space-3)">
      <h3 class="field-group-label">Email Addresses</h3>
      <button class="btn btn--sm btn--secondary"
              hx-get="/admin/orgs/{{ org.id }}/contacts/new-row/?contact_type=email"
              hx-target="#emails-table tbody"
              hx-swap="afterbegin"
              type="button">+ Add email</button>
    </div>
    <div class="table-wrapper">
      <table id="emails-table" class="data-table">
        <thead><tr><th scope="col">Address</th><th scope="col">Label</th><th scope="col"></th></tr></thead>
        <tbody>
          {% for c in email_contacts %}
          {% include "admin/orgs/partials/_contact_row.html" %}
          {% else %}
          <tr><td colspan="3" style="text-align:center;color:var(--color-text-muted)">No email addresses</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin:var(--space-5) 0 var(--space-3)">
      <h3 class="field-group-label">Phone Numbers</h3>
      <button class="btn btn--sm btn--secondary"
              hx-get="/admin/orgs/{{ org.id }}/contacts/new-row/?contact_type=phone"
              hx-target="#phones-table tbody"
              hx-swap="afterbegin"
              type="button">+ Add phone</button>
    </div>
    <div class="table-wrapper">
      <table id="phones-table" class="data-table">
        <thead><tr><th scope="col">Number</th><th scope="col">Label</th><th scope="col"></th></tr></thead>
        <tbody>
          {% for c in phone_contacts %}
          {% include "admin/orgs/partials/_contact_row.html" %}
          {% else %}
          <tr><td colspan="3" style="text-align:center;color:var(--color-text-muted)">No phone numbers</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div style="display:flex;align-items:center;justify-content:space-between;margin:var(--space-5) 0 var(--space-3)">
      <h3 class="field-group-label">Locations</h3>
      <button class="btn btn--sm btn--secondary"
              hx-get="/admin/orgs/{{ org.id }}/addresses/new-row/"
              hx-target="#addresses-table tbody"
              hx-swap="afterbegin"
              type="button">+ Add location</button>
    </div>
    <div class="table-wrapper">
      <table id="addresses-table" class="data-table">
        <thead><tr><th scope="col">Address</th><th scope="col">Type</th><th scope="col">Label</th><th scope="col"></th></tr></thead>
        <tbody>
          {% for a in addresses %}
          {% include "admin/orgs/partials/_address_row.html" %}
          {% else %}
          <tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No locations</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

  </div>
</section>
```

Note: the old Addresses table had 5 columns (Address, City, Region, Postal, Actions). The new Locations table consolidates to 4 (Address — which shows the joined line, Type, Label, Actions) matching the existing `_address_row.html` which already renders a combined address string.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs.py -v -m integration
```

Expected: all pass. Also verify existing contacts tests still pass:

```bash
uv run pytest tests/api/admin/test_orgs_contacts.py tests/api/admin/test_orgs_addresses.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/detail.html
git commit -m "#29 feat: Contact Information section — Email, Phone, Locations subsections"
```

---

## Task 6: `detail.html` — Links and Identifiers `+ Add` buttons into header rows

**Files:**
- Modify: `src/templates/admin/orgs/detail.html`

- [ ] **Step 1: Write failing tests**

```python
def test_org_detail_links_add_button_in_header(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    # Add link button should appear before the table (in the header row), not after
    links_idx = r.text.find('id="links-table"')
    add_link_idx = r.text.find("+ Add link")
    assert add_link_idx < links_idx  # button comes before table


def test_org_detail_identifiers_add_button_in_header(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    idents_idx = r.text.find('id="identifiers-table"')
    add_idents_idx = r.text.find("+ Add identifier")
    assert add_idents_idx < idents_idx
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs.py::test_org_detail_links_add_button_in_header tests/api/admin/test_orgs.py::test_org_detail_identifiers_add_button_in_header -v -m integration
```

- [ ] **Step 3: Update Links section in `detail.html`**

Replace the Links section (currently `<section class="entity-section">` with `<h2>Links</h2>`):

```jinja
<section class="entity-section">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2>Links</h2>
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/orgs/{{ org.id }}/links/new-row/"
            hx-target="#links-table tbody"
            hx-swap="afterbegin"
            type="button">+ Add link</button>
  </div>
  <div class="table-wrapper">
    <table id="links-table" class="data-table">
      <thead><tr><th scope="col">Type</th><th scope="col">URL</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
      <tbody>
        {% for l in links %}
        {% include "admin/orgs/partials/_link_row.html" %}
        {% else %}
        <tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No links</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
```

Replace the Identifiers section:

```jinja
<section class="entity-section">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2>Identifiers</h2>
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/orgs/{{ org.id }}/identifiers/new-row/"
            hx-target="#identifiers-table tbody"
            hx-swap="afterbegin"
            type="button">+ Add identifier</button>
  </div>
  <div class="table-wrapper">
    <table id="identifiers-table" class="data-table">
      <thead><tr><th scope="col">Type</th><th scope="col">Value</th><th scope="col"></th></tr></thead>
      <tbody>
        {% for ident in identifiers %}
        {% include "admin/orgs/partials/_identifier_row.html" %}
        {% else %}
        <tr><td colspan="3" style="text-align:center;color:var(--color-text-muted)">No identifiers</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/detail.html tests/api/admin/test_orgs.py
git commit -m "#29 feat: Links + Identifiers — move + Add button to section header row"
```

---

## Task 7: `_address_form_row.html` — form-group wrappers + button group

**Files:**
- Modify: `src/templates/admin/orgs/partials/_address_form_row.html`
- Modify: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write failing test**

Add to `tests/api/admin/test_orgs_addresses.py`:

```python
def test_address_form_row_has_form_group(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py::test_address_form_row_has_form_group -v -m integration
```

- [ ] **Step 3: Rewrite `_address_form_row.html`**

```jinja
{# admin/orgs/partials/_address_form_row.html #}
<tr id="{% if a %}address-row-{{ a.id }}{% else %}address-row-new{% endif %}">
  <td colspan="4" style="padding:var(--space-2) var(--space-4)">
    <form {% if a %}
          hx-post="/admin/orgs/{{ org_id }}/addresses/{{ a.id }}/edit-row/"
          hx-target="#address-row-{{ a.id }}"
          {% else %}
          hx-post="/admin/orgs/{{ org_id }}/addresses/"
          hx-target="#address-row-new"
          {% endif %}
          hx-swap="outerHTML"
          style="display:grid;gap:var(--space-2)">
      <div class="form-group" style="margin-bottom:0">
        <input type="text" name="address_line_1" placeholder="Address line 1"
               value="{{ a.address_line_1 or '' if a else '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0">
        <input type="text" name="address_line_2" placeholder="Address line 2 (optional)"
               value="{{ a.address_line_2 or '' if a else '' }}">
      </div>
      <div style="display:flex;gap:var(--space-2)">
        <div class="form-group" style="margin-bottom:0;flex:2">
          <input type="text" name="city" placeholder="City"
                 value="{{ a.city or '' if a else '' }}">
        </div>
        <div class="form-group" style="margin-bottom:0;flex:1">
          <input type="text" name="region" placeholder="State" maxlength="2"
                 value="{{ a.region or '' if a else '' }}">
        </div>
        <div class="form-group" style="margin-bottom:0;flex:1">
          <input type="text" name="postal_code" placeholder="ZIP"
                 value="{{ a.postal_code or '' if a else '' }}">
        </div>
      </div>
      <div style="display:flex;gap:var(--space-2);align-items:center">
        <div class="form-group" style="margin-bottom:0;flex:1">
          <select name="address_type">
            {% for t in ('mailing', 'physical', 'other') %}
            <option value="{{ t }}"{% if a and a.address_type == t %} selected{% endif %}>{{ t }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="form-group" style="margin-bottom:0;flex:2">
          <input type="text" name="display_name" placeholder="Label (optional)"
                 value="{{ a.display_name or '' if a else '' }}">
        </div>
        <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
          <button type="submit" class="btn btn--sm btn--primary">Save</button>
          <button type="button" class="btn btn--sm btn--secondary"
                  {% if a %}
                  hx-get="/admin/orgs/{{ org_id }}/addresses/{{ a.id }}/read-row/"
                  hx-target="#address-row-{{ a.id }}"
                  hx-swap="outerHTML"
                  {% else %}
                  onclick="this.closest('tr').remove()"
                  {% endif %}>Cancel</button>
        </div>
      </div>
    </form>
  </td>
</tr>
```

Note: colspan updated from 5 to 4 to match the new Locations table (Address, Type, Label, Actions).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/partials/_address_form_row.html tests/api/admin/test_orgs_addresses.py
git commit -m "#29 feat: address form row — form-group wrappers, button group"
```

---

## Task 8: `_link_form_row.html` — form-group, pill toggles, button group

**Files:**
- Modify: `src/templates/admin/orgs/partials/_link_form_row.html`
- Modify: `tests/api/admin/test_orgs_links.py`

The link form has two boolean fields (`is_active`, `is_canonical`). Replace bare `<label><input type="checkbox">` with `.toggle` pill components. No `hx-post` on these — they're regular form checkboxes, not auto-saving.

- [ ] **Step 1: Write failing tests**

Add to `tests/api/admin/test_orgs_links.py`:

```python
def test_link_form_row_has_form_group(client, org_and_link):
    oid, _ = org_and_link
    r = client.get(f"/admin/orgs/{oid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


def test_link_form_row_has_toggle(client, org_and_link):
    oid, _ = org_and_link
    r = client.get(f"/admin/orgs/{oid}/links/new-row/", headers=HTMX_HEADERS)
    assert "toggle__track" in r.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs_links.py::test_link_form_row_has_form_group tests/api/admin/test_orgs_links.py::test_link_form_row_has_toggle -v -m integration
```

- [ ] **Step 3: Rewrite `_link_form_row.html`**

```jinja
{# admin/orgs/partials/_link_form_row.html #}
<tr id="{% if l %}link-row-{{ l.id }}{% else %}link-row-new{% endif %}">
  <td colspan="4" style="padding:var(--space-2) var(--space-4)">
    <form {% if l %}
          hx-post="/admin/orgs/{{ org_id }}/links/{{ l.id }}/edit-row/"
          hx-target="#link-row-{{ l.id }}"
          {% else %}
          hx-post="/admin/orgs/{{ org_id }}/links/"
          hx-target="#link-row-new"
          {% endif %}
          hx-swap="outerHTML"
          style="display:flex;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:10rem">
        <input type="url" name="url" required
               value="{{ l.url if l else '' }}" placeholder="https://">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <select name="link_type_id">
          <optgroup label="Social">
            {% for lt in link_types if lt.is_social %}
            <option value="{{ lt.id }}"{% if l and l.link_type_id == lt.id %} selected{% endif %}>
              {{ lt.display_name }}</option>
            {% endfor %}
          </optgroup>
          <optgroup label="General">
            {% for lt in link_types if not lt.is_social %}
            <option value="{{ lt.id }}"{% if l and l.link_type_id == lt.id %} selected{% endif %}>
              {{ lt.display_name }}</option>
            {% endfor %}
          </optgroup>
        </select>
      </div>
      <label class="toggle" style="flex-shrink:0">
        <input type="checkbox" name="is_active" value="true"
               {% if l and l.is_active or not l %} checked{% endif %}>
        <span class="toggle__track"><span class="toggle__thumb"></span></span>
        <span class="toggle__label" style="font-size:var(--font-size-sm);color:var(--color-text-muted)">Active</span>
      </label>
      <label class="toggle" style="flex-shrink:0">
        <input type="checkbox" name="is_canonical" value="true"
               {% if l and l.is_canonical %} checked{% endif %}>
        <span class="toggle__track"><span class="toggle__thumb"></span></span>
        <span class="toggle__label" style="font-size:var(--font-size-sm);color:var(--color-text-muted)">Canonical</span>
      </label>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if l %}
                hx-get="/admin/orgs/{{ org_id }}/links/{{ l.id }}/read-row/"
                hx-target="#link-row-{{ l.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs_links.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/partials/_link_form_row.html tests/api/admin/test_orgs_links.py
git commit -m "#29 feat: link form row — form-group, pill toggles, button group"
```

---

## Task 9: `_identifier_form_row.html` — form-group + button group

**Files:**
- Modify: `src/templates/admin/orgs/partials/_identifier_form_row.html`
- Modify: `tests/api/admin/test_orgs_identifiers.py`

- [ ] **Step 1: Write failing test**

Use `org_id_and_type` (yields `(oid, type_id)` — 2-tuple) rather than `org_and_identifier` (3-tuple), since the test only needs an org with a valid identifier type to hit `new-row/`.

Add to `tests/api/admin/test_orgs_identifiers.py`:

```python
def test_identifier_form_row_has_form_group(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/admin/test_orgs_identifiers.py::test_identifier_form_row_has_form_group -v -m integration
```

- [ ] **Step 3: Rewrite `_identifier_form_row.html`**

```jinja
{# admin/orgs/partials/_identifier_form_row.html #}
<tr id="{% if ident %}identifier-row-{{ ident.id }}{% else %}identifier-row-new{% endif %}">
  <td colspan="3" style="padding:var(--space-2) var(--space-4)">
    <form {% if ident %}
          hx-post="/admin/orgs/{{ org_id }}/identifiers/{{ ident.id }}/edit-row/"
          hx-target="#identifier-row-{{ ident.id }}"
          {% else %}
          hx-post="/admin/orgs/{{ org_id }}/identifiers/"
          hx-target="#identifier-row-new"
          {% endif %}
          hx-swap="outerHTML"
          style="display:flex;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;min-width:8rem">
        <select name="entity_identifier_type_id">
          {% for it in ident_types %}
          <option value="{{ it.id }}"
                  {% if ident and ident.entity_identifier_type_id == it.id %} selected{% endif %}>
            {{ it.display_name }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1">
        <input type="text" name="value" required
               value="{{ ident.value if ident else '' }}" placeholder="Value">
      </div>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if ident %}
                hx-get="/admin/orgs/{{ org_id }}/identifiers/{{ ident.id }}/read-row/"
                hx-target="#identifier-row-{{ ident.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs_identifiers.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/partials/_identifier_form_row.html tests/api/admin/test_orgs_identifiers.py
git commit -m "#29 feat: identifier form row — form-group, button group"
```

---

## Task 10: `_child_form_row.html` — form-group on search input

**Files:**
- Modify: `src/templates/admin/orgs/partials/_child_form_row.html`
- Modify: `tests/api/admin/test_orgs_children.py`

- [ ] **Step 1: Write failing test**

Use the existing `parent_and_child` fixture (yields `(pid, cid)`) — there is no `org_id` fixture in this file.

Add to `tests/api/admin/test_orgs_children.py`:

```python
def test_child_form_row_has_form_group(client, parent_and_child):
    pid, _ = parent_and_child
    r = client.get(f"/admin/orgs/{pid}/children/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/api/admin/test_orgs_children.py::test_child_form_row_has_form_group -v -m integration
```

- [ ] **Step 3: Rewrite `_child_form_row.html`**

Wrap the search input in a `form-group` div. Keep the `position:relative` on the wrapper (needed for the dropdown listbox positioning). Apply button group pattern.

```jinja
{# admin/orgs/partials/_child_form_row.html #}
<tr id="child-row-new">
  <td colspan="3" style="padding:var(--space-2) var(--space-4)">
    <form hx-post="/admin/orgs/{{ org_id }}/children/"
          hx-target="#children-table tbody"
          hx-swap="afterbegin"
          style="display:flex;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;flex:1;position:relative">
        <input type="text" autocomplete="off" placeholder="Search for an organization…"
               id="child-search-display"
               hx-get="/admin/orgs/search/"
               hx-trigger="input changed delay:200ms"
               hx-target="#child-search-results"
               hx-params="q"
               name="child-display">
        <input type="hidden" name="child_id" id="child-id-hidden">
        <ul id="child-search-results" role="listbox"
            style="position:absolute;background:var(--color-surface-1);border:1px solid var(--color-border);list-style:none;margin:0;padding:0;width:100%;z-index:10"></ul>
      </div>
      <div style="display:flex;gap:var(--space-2);white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Add</button>
        <button type="button" class="btn btn--sm btn--secondary"
                onclick="this.closest('tr').remove()">Cancel</button>
      </div>
    </form>
    <script>
      document.getElementById('child-search-results').addEventListener('click', function(e) {
        const li = e.target.closest('[data-id]');
        if (!li) return;
        document.getElementById('child-id-hidden').value = li.dataset.id;
        document.getElementById('child-search-display').value = li.dataset.label;
        this.innerHTML = '';
      });
    </script>
  </td>
</tr>
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/admin/test_orgs_children.py -v -m integration
```

- [ ] **Step 5: Run full non-integration suite to check for regressions**

```bash
uv run pytest -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/orgs/partials/_child_form_row.html tests/api/admin/test_orgs_children.py
git commit -m "#29 feat: child form row — form-group on search input, button group"
```

---

## Final verification

- [ ] **Run all integration tests for affected files**

```bash
uv run pytest tests/api/admin/test_orgs.py \
              tests/api/admin/test_orgs_contacts.py \
              tests/api/admin/test_orgs_addresses.py \
              tests/api/admin/test_orgs_links.py \
              tests/api/admin/test_orgs_identifiers.py \
              tests/api/admin/test_orgs_children.py \
              tests/api/admin/test_orgs_inline.py \
              -v -m integration
```

Expected: all pass.

- [ ] **Run linter**

```bash
uv run ruff check .
```

Expected: no errors.
