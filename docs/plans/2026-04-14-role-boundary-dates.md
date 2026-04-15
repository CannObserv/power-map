# Role Boundary Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional `established_on` / `abolished_on` date fields to roles; enforce that any assignment dates fall within those boundaries (hard block).

**Architecture:** Schema migration adds two nullable `DATE` columns and a DB-level intra-row ordering constraint. A pure helper function handles cross-object boundary validation. Application-level enforcement in the two assignment mutation routes (PostgreSQL CHECK can't span tables). New `/inline/dates/` routes on role detail follow the existing read/edit/save toggle pattern.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Jinja2 + HTMX, PostgreSQL 15+, pytest (integration tests require `TEST_DATABASE_URL`)

---

## File Map

| Action | File |
|--------|------|
| Modify | `src/core/schema.sql` |
| Modify | `src/api/admin/roles_detail.py` |
| Modify | `src/api/admin/roles_assignments_inline.py` |
| Create | `src/templates/admin/roles/partials/_dates_read.html` |
| Create | `src/templates/admin/roles/partials/_dates_form.html` |
| Modify | `src/templates/admin/roles/detail.html` |
| Modify | `src/templates/admin/roles/partials/_assignment_form_row.html` |
| Modify | `src/templates/admin/roles/partials/_assignment_edit_row.html` |
| Modify | `tests/api/admin/test_roles_detail_inline.py` |
| Modify | `tests/api/admin/test_roles_assignments_inline.py` |

**Run all tests from:** `.worktrees/101-role-boundary-dates/`

**Load env before integration tests:**
```bash
export $(cat .env | xargs) 2>/dev/null
```

---

## Task 1: Schema migration

**Files:**
- Modify: `src/core/schema.sql` (after line 319, before the org names/acronyms section)
- Test: `tests/api/admin/test_roles_detail_inline.py`

### Context

`schema.sql` uses `IF NOT EXISTS` guards for column additions and `table_constraints` checks for new constraints. Mirror that pattern exactly.

The column additions section ends at line 319 (after `role_assignments.archived_at`). Insert the new blocks immediately after that, before the `-- Organization names/acronyms schema migration` comment.

- [ ] **Step 1: Write the failing DB constraint test**

Add to `tests/api/admin/test_roles_detail_inline.py` after the existing `role_id` fixture:

```python
# ---------------------------------------------------------------------------
# Schema constraint: chk_role_date_order
# ---------------------------------------------------------------------------


async def test_chk_role_date_order_rejects_inverted_dates(db):
    """established_on > abolished_on must raise CheckViolationError."""
    oid = await _make_org(db, "Boundary Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Test Role",
    )
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
            datetime.date(2020, 1, 1), datetime.date(2010, 1, 1), rid,
        )


async def test_chk_role_date_order_allows_same_date(db):
    """established_on == abolished_on is valid (single-day role)."""
    oid = await _make_org(db, "Same Day Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "One Day Role",
    )
    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        datetime.date(2020, 6, 15), datetime.date(2020, 6, 15), rid,
    )
    row = await db.fetchrow("SELECT established_on, abolished_on FROM roles WHERE id=$1", rid)
    assert row["established_on"] == datetime.date(2020, 6, 15)
    assert row["abolished_on"] == datetime.date(2020, 6, 15)
```

Add `import datetime` to the top imports of the test file.

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/api/admin/test_roles_detail_inline.py::test_chk_role_date_order_rejects_inverted_dates -v
```

Expected: `FAILED` — `CheckViolationError` not raised because the column doesn't exist yet.

- [ ] **Step 3: Add schema migration blocks**

In `src/core/schema.sql`, after line 319 (`END $$;` for `role_assignments.archived_at`), insert:

```sql
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='established_on'
    ) THEN
        ALTER TABLE roles ADD COLUMN established_on DATE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='abolished_on'
    ) THEN
        ALTER TABLE roles ADD COLUMN abolished_on DATE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='roles' AND constraint_name='chk_role_date_order'
    ) THEN
        ALTER TABLE roles ADD CONSTRAINT chk_role_date_order
            CHECK (established_on IS NULL OR abolished_on IS NULL
                   OR established_on <= abolished_on);
    END IF;
END $$;
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/api/admin/test_roles_detail_inline.py::test_chk_role_date_order_rejects_inverted_dates tests/api/admin/test_roles_detail_inline.py::test_chk_role_date_order_allows_same_date -v
```

Expected: both `PASSED`.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest --no-cov -q
```

Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/api/admin/test_roles_detail_inline.py
git commit -m "#101 feat: add established_on/abolished_on columns to roles with ordering constraint"
```

---

## Task 2: Boundary validation helper

**Files:**
- Modify: `src/api/admin/roles_assignments_inline.py` (add helper after `_parse_date`)
- Test: `tests/api/admin/test_roles_assignments_inline.py`

### Context

`_check_assignment_within_bounds` is a pure function — no DB access, no HTTP. Tests for it are unit tests (no `integration` mark, no DB fixture needed).

- [ ] **Step 1: Write the failing unit tests**

Add to `tests/api/admin/test_roles_assignments_inline.py`, after the existing imports and before the `pytestmark` line (so they run without the integration mark):

```python
import datetime as dt

from src.api.admin.roles_assignments_inline import _check_assignment_within_bounds

# ---------------------------------------------------------------------------
# Unit tests: _check_assignment_within_bounds
# ---------------------------------------------------------------------------


def test_bounds_no_constraints_returns_none():
    assert _check_assignment_within_bounds(None, None, None, None) is None


def test_bounds_start_before_established_returns_error():
    err = _check_assignment_within_bounds(
        dt.date(2009, 12, 31), None, dt.date(2010, 1, 1), None
    )
    assert err is not None
    assert "established" in err.lower()


def test_bounds_start_on_established_ok():
    assert _check_assignment_within_bounds(
        dt.date(2010, 1, 1), None, dt.date(2010, 1, 1), None
    ) is None


def test_bounds_end_after_abolished_returns_error():
    err = _check_assignment_within_bounds(
        None, dt.date(2021, 1, 1), None, dt.date(2020, 12, 31)
    )
    assert err is not None
    assert "abolished" in err.lower()


def test_bounds_end_on_abolished_ok():
    assert _check_assignment_within_bounds(
        None, dt.date(2020, 12, 31), None, dt.date(2020, 12, 31)
    ) is None


def test_bounds_start_after_abolished_returns_error():
    err = _check_assignment_within_bounds(
        dt.date(2021, 6, 1), None, None, dt.date(2020, 12, 31)
    )
    assert err is not None


def test_bounds_end_before_established_returns_error():
    err = _check_assignment_within_bounds(
        None, dt.date(2009, 1, 1), dt.date(2010, 1, 1), None
    )
    assert err is not None


def test_bounds_null_dates_with_constraints_ok():
    """Null assignment dates are valid regardless of role bounds."""
    assert _check_assignment_within_bounds(
        None, None, dt.date(2010, 1, 1), dt.date(2020, 12, 31)
    ) is None


def test_bounds_within_range_ok():
    assert _check_assignment_within_bounds(
        dt.date(2015, 1, 1), dt.date(2019, 12, 31),
        dt.date(2010, 1, 1), dt.date(2020, 12, 31),
    ) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/api/admin/test_roles_assignments_inline.py::test_bounds_no_constraints_returns_none -v
```

Expected: `FAILED` — `ImportError` because `_check_assignment_within_bounds` doesn't exist yet.

- [ ] **Step 3: Implement the helper**

In `src/api/admin/roles_assignments_inline.py`, add after the `_parse_date` function:

```python
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
```

- [ ] **Step 4: Run unit tests to confirm they pass**

```bash
uv run pytest tests/api/admin/test_roles_assignments_inline.py -k "test_bounds_" -v
```

Expected: all 9 `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/roles_assignments_inline.py tests/api/admin/test_roles_assignments_inline.py
git commit -m "#101 feat: add _check_assignment_within_bounds helper"
```

---

## Task 3: Assignment boundary enforcement

**Files:**
- Modify: `src/api/admin/roles_detail.py` — extend `_get_role` SELECT
- Modify: `src/api/admin/roles_assignments_inline.py` — apply helper in create/edit routes; pass role context to form templates
- Modify: `src/templates/admin/roles/partials/_assignment_form_row.html` — add boundary hint
- Modify: `src/templates/admin/roles/partials/_assignment_edit_row.html` — add boundary hint
- Test: `tests/api/admin/test_roles_assignments_inline.py`

### Context

`_get_role` is imported by `roles_assignments_inline.py` — extending its SELECT automatically exposes `established_on` / `abolished_on` everywhere that calls it. The form templates currently receive `role_id` but not `role`; both need `role` added to their context so the hint can render. The `assignment_edit_row_get` and `assignment_edit_row_post` routes don't currently call `_get_role` — they need it added.

The integration tests use the `role_id` fixture (which inserts a bare role with no boundary dates). Add a new `bounded_role_id` fixture with both dates set.

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/api/admin/test_roles_assignments_inline.py`:

```python
@pytest.fixture
async def bounded_role_id(db):
    """Role with established_on=2010-01-01, abolished_on=2020-12-31."""
    oid = await _make_org(db, "Bounded Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, established_on, abolished_on)"
        " VALUES ($1, $2, $3, $4, $5)",
        rid, oid, "Bounded Director",
        datetime.date(2010, 1, 1), datetime.date(2020, 12, 31),
    )
    return rid


# ---------------------------------------------------------------------------
# Boundary enforcement: create
# ---------------------------------------------------------------------------


async def test_create_start_before_established_returns_error(
    client, bounded_role_id, person_id
):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2009-12-31", "end_date": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_end_after_abolished_returns_error(
    client, bounded_role_id, person_id
):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2015-01-01",
            "end_date": "2021-01-01",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_within_bounds_succeeds(client, bounded_role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2015-01-01",
            "end_date": "2019-12-31",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT id FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        bounded_role_id, person_id,
    )
    assert row is not None


# ---------------------------------------------------------------------------
# Boundary enforcement: edit
# ---------------------------------------------------------------------------


async def _make_assignment(db, role_id, person_id) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, FALSE)",
        aid, person_id, role_id,
    )
    return aid


async def test_edit_start_before_established_returns_error(
    client, bounded_role_id, person_id, db
):
    aid = await _make_assignment(db, bounded_role_id, person_id)
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/{aid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2009-06-01", "end_date": "", "is_current": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_edit_end_after_abolished_returns_error(
    client, bounded_role_id, person_id, db
):
    aid = await _make_assignment(db, bounded_role_id, person_id)
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/{aid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2015-01-01", "end_date": "2021-06-01", "is_current": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
```

Add `import datetime` to the top imports (if not already added in Task 2).

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/api/admin/test_roles_assignments_inline.py -k "test_create_start_before" -v
```

Expected: `FAILED` — no boundary check in the route yet.

- [ ] **Step 3: Extend `_get_role` SELECT**

In `src/api/admin/roles_detail.py`, in `_get_role`, add `r.established_on, r.abolished_on` to the SELECT:

```python
    row = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  r.established_on, r.abolished_on,
                  r.organization_id AS org_id,
                  dn.display_name AS org_name
           FROM roles r
           LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
           WHERE r.id = $1""",
        role_id,
    )
```

- [ ] **Step 4: Apply boundary check in `assignment_create`**

In `src/api/admin/roles_assignments_inline.py`:

1. Change `await _get_role(role_id, db)  # 404 check` to `role = await _get_role(role_id, db)` at the top of `assignment_create`.

2. After the date-parse try/except block (after `end_date_val` is set), add:

```python
    bound_err = _check_assignment_within_bounds(
        start_date_val, end_date_val,
        role["established_on"], role["abolished_on"],
    )
    if bound_err:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "role": role,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", bound_err),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
```

3. Also add `"role": role` to the context dict in all other error-path `TemplateResponse` calls inside `assignment_create` (person validation error, date parse error, check violation error, unique violation error) and to the `assignment_new_row` success response.

In `assignment_new_row`, the current code discards the `_get_role` result. Change to:
```python
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_form_row.html",
        {
            "role_id": role_id,
            "role": role,
            "start_date_input": "",
            "end_date_input": "",
            "is_current_input": False,
        },
    )
```

- [ ] **Step 5: Apply boundary check in `assignment_edit_row_post`**

In `assignment_edit_row_post`:

1. Add `role = await _get_role(role_id, db)` immediately after `_get_assignment`.

2. Update `_error_ctx()` to include `"role": role`:

```python
    def _error_ctx():
        return {
            "ra": ra,
            "role_id": role_id,
            "role": role,
            "start_date_input": start_date,
            "end_date_input": end_date,
            "is_current_input": is_current_val,
        }
```

3. After the date-parse try/except, add boundary check before the DB UPDATE:

```python
    bound_err = _check_assignment_within_bounds(
        start_date_val, end_date_val,
        role["established_on"], role["abolished_on"],
    )
    if bound_err:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", bound_err),
                "HX-Retarget": f"#assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )
```

In `assignment_edit_row_get`, add `role = await _get_role(role_id, db)` and pass `"role": role` to the template context.

- [ ] **Step 6: Add boundary hint to `_assignment_form_row.html`**

In `src/templates/admin/roles/partials/_assignment_form_row.html`, inside the `<td colspan="5">`, add this block immediately before the `<form>` tag:

```html
    {% if role and (role.established_on or role.abolished_on) %}
    <p style="margin:0 0 var(--space-2);font-size:var(--font-size-xs);color:var(--color-text-muted)">
      Role active:
      {% if role.established_on %}{{ role.established_on }}{% else %}?{% endif %}
      –
      {% if role.abolished_on %}{{ role.abolished_on }}{% else %}present{% endif %}
    </p>
    {% endif %}
```

- [ ] **Step 7: Add boundary hint to `_assignment_edit_row.html`**

In `src/templates/admin/roles/partials/_assignment_edit_row.html`, add `title` attributes to both date inputs when role bounds exist. Replace the start date input with:

```html
      <input type="date" name="start_date" title="Start date{% if role and (role.established_on or role.abolished_on) %} (role: {{ role.established_on or '?' }} – {{ role.abolished_on or 'present' }}){% endif %}"
             id="start-date-input-{{ ra.id }}"
             value="{{ start_date_input }}">
```

And the end date input with:

```html
      <input type="date" name="end_date" title="End date{% if role and (role.established_on or role.abolished_on) %} (role: {{ role.established_on or '?' }} – {{ role.abolished_on or 'present' }}){% endif %}"
             id="end-date-input-{{ ra.id }}"
             value="{{ end_date_input }}"
             {% if is_current_input %}disabled{% endif %}>
```

- [ ] **Step 8: Run all boundary enforcement tests**

```bash
uv run pytest tests/api/admin/test_roles_assignments_inline.py -v
```

Expected: all tests `PASSED` (including both unit and integration).

- [ ] **Step 9: Run full suite**

```bash
uv run pytest --no-cov -q
```

Expected: 0 failures.

- [ ] **Step 10: Commit**

```bash
git add src/api/admin/roles_detail.py src/api/admin/roles_assignments_inline.py \
        src/templates/admin/roles/partials/_assignment_form_row.html \
        src/templates/admin/roles/partials/_assignment_edit_row.html \
        tests/api/admin/test_roles_assignments_inline.py
git commit -m "#101 feat: enforce role boundary dates on assignment create/edit"
```

---

## Task 4: Dates inline routes + templates

**Files:**
- Modify: `src/api/admin/roles_detail.py` — add `/inline/dates/` routes
- Create: `src/templates/admin/roles/partials/_dates_read.html`
- Create: `src/templates/admin/roles/partials/_dates_form.html`
- Modify: `src/templates/admin/roles/detail.html` — include dates partial
- Test: `tests/api/admin/test_roles_detail_inline.py`

### Context

The inline pattern is: `GET /inline/dates/` → read partial; `GET /inline/dates/edit/` → form partial; `POST /inline/dates/` → validate, save, return read partial. Both partials target `id="dates-field"`. Follow `_notes_read.html` / `_notes_form.html` as the closest template analogue.

The POST must:
1. Parse and validate both date inputs (may be empty → `None`).
2. Check that `established_on <= abolished_on` if both set (gives a friendlier error than letting the DB constraint fire, though the constraint is the backstop).
3. Fetch all active (non-archived) assignments for the role. For each, call `_check_assignment_within_bounds`. If any fail, return the form partial with a flash error showing the count.
4. Save and return the read partial.

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/api/admin/test_roles_detail_inline.py`:

```python
# ---------------------------------------------------------------------------
# Dates inline
# ---------------------------------------------------------------------------


async def test_dates_read_returns_partial(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/dates/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"dates-field" in r.content


async def test_dates_edit_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/dates/edit/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"established_on" in r.content
    assert b"abolished_on" in r.content


async def test_dates_post_saves_both_dates(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": "2020-12-31"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT established_on, abolished_on FROM roles WHERE id=$1", role_id
    )
    assert str(row["established_on"]) == "2010-01-01"
    assert str(row["abolished_on"]) == "2020-12-31"


async def test_dates_post_clears_dates(client, role_id, db):
    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        datetime.date(2010, 1, 1), datetime.date(2020, 12, 31), role_id,
    )
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT established_on, abolished_on FROM roles WHERE id=$1", role_id
    )
    assert row["established_on"] is None
    assert row["abolished_on"] is None


async def test_dates_post_rejects_inverted_order(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2020-01-01", "abolished_on": "2010-01-01"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"established_on" in r.content  # form re-rendered


async def test_dates_post_rejects_when_assignments_outside_bounds(
    client, role_id, db
):
    """Saving bounds that would exclude an existing assignment must fail."""
    # Create a person and assignment with start_date in 2005
    pid = await _make_person(db, "Early Bird")
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)"
        " VALUES ($1, $2, $3, FALSE, $4)",
        generate_id(), pid, role_id, datetime.date(2005, 3, 1),
    )
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"established_on" in r.content  # form re-rendered


async def test_dates_post_returns_success_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
```

- [ ] **Step 2: Run a test to confirm it fails**

```bash
uv run pytest tests/api/admin/test_roles_detail_inline.py::test_dates_read_returns_partial -v
```

Expected: `FAILED` — 404 or 405 (route doesn't exist).

- [ ] **Step 3: Create `_dates_read.html`**

Create `src/templates/admin/roles/partials/_dates_read.html`:

```html
{# admin/roles/partials/_dates_read.html — boundary dates read partial #}
<div id="dates-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">Boundary Dates</h3>
    {% if not role.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/roles/{{ role.id }}/inline/dates/edit/"
            hx-target="#dates-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="display:flex;gap:var(--space-6);font-size:var(--font-size-sm)">
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">Established</div>
      <div style="color:{% if role.established_on %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ role.established_on or '—' }}
      </div>
    </div>
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">Abolished</div>
      <div style="color:{% if role.abolished_on %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ role.abolished_on or '—' }}
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Create `_dates_form.html`**

Create `src/templates/admin/roles/partials/_dates_form.html`:

```html
{# admin/roles/partials/_dates_form.html — boundary dates edit form #}
<div id="dates-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/roles/{{ role.id }}/inline/dates/"
        hx-target="#dates-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <span class="field-group-label">Boundary Dates</span>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/roles/{{ role.id }}/inline/dates/"
                hx-target="#dates-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    <div style="display:flex;gap:var(--space-4);flex-wrap:wrap">
      <div class="form-group" style="margin-bottom:0">
        <label for="established-on-input" style="font-size:var(--font-size-xs)">Established</label>
        <input type="date" id="established-on-input" name="established_on"
               value="{{ established_on_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0">
        <label for="abolished-on-input" style="font-size:var(--font-size-xs)">Abolished</label>
        <input type="date" id="abolished-on-input" name="abolished_on"
               value="{{ abolished_on_input or '' }}">
      </div>
    </div>
  </form>
</div>
```

- [ ] **Step 5: Add inline dates routes to `roles_detail.py`**

Add to `src/api/admin/roles_detail.py` after the notes inline section:

```python
# ---------------------------------------------------------------------------
# Boundary dates inline
# ---------------------------------------------------------------------------


@router.get("/inline/dates/")
async def role_inline_dates_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return boundary dates read partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_dates_read.html", {"role": role}
    )


@router.get("/inline/dates/edit/")
async def role_inline_dates_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return boundary dates edit form partial."""
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_dates_form.html",
        {
            "role": role,
            "established_on_input": role["established_on"].isoformat() if role["established_on"] else "",
            "abolished_on_input": role["abolished_on"].isoformat() if role["abolished_on"] else "",
        },
    )


@router.post("/inline/dates/")
async def role_inline_dates_post(
    role_id: str,
    request: Request,
    established_on: str = Form(""),
    abolished_on: str = Form(""),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save boundary dates; validate against existing assignments."""
    from src.api.admin.roles_assignments_inline import _check_assignment_within_bounds

    role = await _get_role(role_id, db)

    def _form_ctx(est_input: str, abol_input: str):
        return {
            "role": role,
            "established_on_input": est_input,
            "abolished_on_input": abol_input,
        }

    try:
        established_on_val = _parse_date(established_on)
        abolished_on_val = _parse_date(abolished_on)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
        )

    if established_on_val and abolished_on_val and established_on_val > abolished_on_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", "Established date must be on or before abolished date."),
        )

    # Check existing active assignments
    assignments = await db.fetch(
        """SELECT start_date, end_date FROM role_assignments
           WHERE role_id = $1 AND archived_at IS NULL""",
        role_id,
    )
    violations = [
        ra for ra in assignments
        if _check_assignment_within_bounds(
            ra["start_date"], ra["end_date"], established_on_val, abolished_on_val
        )
    ]
    if violations:
        count = len(violations)
        msg = (
            f"{count} existing assignment{'s' if count > 1 else ''} "
            f"fall{'s' if count == 1 else ''} outside these boundaries."
        )
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_dates_form.html",
            _form_ctx(established_on, abolished_on),
            headers=flash_trigger("error", msg),
        )

    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        established_on_val, abolished_on_val, role_id,
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_dates_read.html",
        {"role": role},
        headers=flash_trigger("success", "Boundary dates saved."),
    )
```

`_parse_date` is defined in `roles_assignments_inline.py`. Add this import at the top of `roles_detail.py`:

```python
from src.api.admin.roles_assignments_inline import _parse_date
```

- [ ] **Step 6: Include dates partial in `detail.html`**

In `src/templates/admin/roles/detail.html`, after the title include and before the notes include:

```html
    <div style="margin-top:var(--space-5)">
      {% include "admin/roles/partials/_title_read.html" %}
    </div>

    <div style="margin-top:var(--space-5)">
      {% include "admin/roles/partials/_dates_read.html" %}
    </div>

    {% include "admin/roles/partials/_notes_read.html" %}
```

- [ ] **Step 7: Run all dates inline tests**

```bash
uv run pytest tests/api/admin/test_roles_detail_inline.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 8: Run full suite**

```bash
uv run pytest --no-cov -q
```

Expected: 0 failures.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin/roles_detail.py \
        src/templates/admin/roles/partials/_dates_read.html \
        src/templates/admin/roles/partials/_dates_form.html \
        src/templates/admin/roles/detail.html \
        tests/api/admin/test_roles_detail_inline.py
git commit -m "#101 feat: add inline boundary dates editing to role detail"
```
