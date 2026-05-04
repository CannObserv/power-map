# Phase 2a — Visibility + Expanded `name_type` + Deadname Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #123 (Phase 2a sub-task)
**Worktree:** `.worktrees/feat/123-phase-2-admin-ui`
**Design:** `docs/plans/2026-05-04-phase-2-person-name-ui-design.md` (§ Phase 2a)

**Goal:** Make the Phase-1 visibility column editable in the admin UI, expand the `name_type` dropdown to all 12 values, surface a per-page session toggle for legal/historical names on person-detail, and confirm the visibility side effect when an admin changes a row to `name_type='deadname'`.

**Architecture:** Extend `_names_shared.py` with a `supports_metadata: bool = False` config flag. When True, the router accepts `visibility` as a Form field and templates render expanded controls. Org name CRUD passes False (default) and is unaffected. The deadname-confirm dialog is JS-only (admin-side, no server round-trip).

**Tech Stack:** FastAPI + Form fields, asyncpg, Jinja2, HTMX, vanilla JS, pytest (unit + integration), Vitest.

---

## File Map

**Modify:**
- `src/api/admin/_names_shared.py` — add `supports_metadata` flag; accept `visibility` Form field; pass through to templates.
- `src/api/admin/people_names.py` — pass `supports_metadata=True`.
- `src/api/admin/people.py` — accept `?show_historical=1`; pass to `detail.html`.
- `src/templates/admin/people/detail.html` — add disclosure toggle.
- `src/templates/admin/people/partials/_name_form_row.html` — add `visibility` select; expand `name_type` options.
- `src/templates/admin/people/partials/_name_row.html` — add visibility badge for non-public rows.
- `src/templates/admin/people/partials/_name_rows.html` — already iterates; no change needed unless we wrap rows by visibility.
- `tests/api/admin/test_people_names.py` (or new `test_people_names_phase2a.py`) — add tests.

**Create:**
- `src/static/admin/person-name-deadname-confirm.js` — confirm dialog wiring.
- `tests/js/person-name-deadname-confirm.test.js` — Vitest coverage.

---

## Pre-flight

- [ ] **Confirm baseline pass count**

```bash
cd /home/exedev/power-map/.worktrees/feat/123-phase-2-admin-ui
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q tests/api/admin/ tests/core/
npm test
```

Record baseline counts. The pre-existing address-normalizer failure may still be present; ignore it.

- [ ] **Boot the dev server in the worktree**

```bash
fuser -k 8001/tcp 2>/dev/null; sleep 1
nohup uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload > /tmp/power-map-dev.log 2>&1 &
sleep 2 && curl -s -o /dev/null -w "Dev: %{http_code}\n" http://localhost:8001/admin/
```

Expect `307`. Manual smoke test follows each task.

---

## Task 1: Backend — `supports_metadata` flag + `visibility` Form field

**Files:**
- Modify: `src/api/admin/_names_shared.py`
- Modify: `src/api/admin/people_names.py`
- Create: `tests/api/admin/test_people_names_visibility.py`

- [ ] **Step 1: Add failing tests**

```python
# tests/api/admin/test_people_names_visibility.py
"""Phase 2a backend tests for person-name visibility metadata."""

import os
import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
async def db():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


async def _seed_person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute("COMMIT")  # outside transaction so TestClient sees it
    return pid


async def test_post_name_with_visibility_legal_only(db, client):
    pid = await _seed_person(db)
    response = client.post(
        f"/admin/people/{pid}/names/",
        data={"name": "Test", "name_type": "legal", "visibility": "legal_only"},
        headers={**AUTH, "HX-Request": "true"},
    )
    assert response.status_code == 200
    row = await db.fetchrow(
        "SELECT visibility FROM person_names WHERE person_id=$1 AND name='Test'", pid
    )
    assert row["visibility"] == "legal_only"


async def test_post_deadname_coerced_to_legal_only(db, client):
    pid = await _seed_person(db)
    client.post(
        f"/admin/people/{pid}/names/",
        data={"name": "Old Name", "name_type": "deadname", "visibility": "public"},
        headers={**AUTH, "HX-Request": "true"},
    )
    row = await db.fetchrow(
        "SELECT visibility FROM person_names WHERE person_id=$1 AND name='Old Name'", pid
    )
    assert row["visibility"] == "legal_only"


@pytest.mark.parametrize(
    "name_type", ["maiden", "religious", "stage", "deadname", "reading", "romanization", "mrz"]
)
async def test_post_name_accepts_new_name_type(db, client, name_type):
    pid = await _seed_person(db)
    response = client.post(
        f"/admin/people/{pid}/names/",
        data={"name": f"T-{name_type}", "name_type": name_type},
        headers={**AUTH, "HX-Request": "true"},
    )
    assert response.status_code == 200, f"Failed for {name_type}: {response.text[:200]}"
```

- [ ] **Step 2: Run tests — verify they fail**

Expected: visibility tests fail (form doesn't accept the field), new name_type tests fail or get rejected by the form's hard-coded enum.

- [ ] **Step 3: Update `_names_shared.py`**

Add `supports_metadata: bool = False` parameter to `make_names_router`. Inside the create + edit routes, accept `visibility: str | None = Form(None)` and include it in the INSERT / UPDATE only when `supports_metadata` is True. Validate in-Python that `visibility in {None, 'public', 'legal_only', 'hidden'}`; on invalid, return 400.

```python
def make_names_router(
    *,
    # ... existing params ...
    supports_metadata: bool = False,
) -> APIRouter:
    ...

    @router.post("/")
    async def create(
        # ... existing params ...
        visibility: str | None = Form(None),
    ):
        if supports_metadata and visibility is not None:
            if visibility not in ('public', 'legal_only', 'hidden'):
                raise HTTPException(400, "invalid visibility")
            cols = "(id, {entity_fk}, name, name_type, is_canonical, visibility)"
            placeholders = "($1, $2, $3, $4, $5, $6)"
            params = (..., visibility)
        else:
            # existing 5-column insert
            ...
```

(Implementation sketch — actual code uses string formatting consistent with the existing pattern.)

- [ ] **Step 4: `people_names.py` passes `supports_metadata=True`**

```python
router = make_names_router(
    # ... existing kwargs ...
    supports_metadata=True,
)
```

- [ ] **Step 5: Run tests — verify they pass**

Expected: all visibility + name_type tests pass.

- [ ] **Step 6: Confirm regressions on org-side stay clean**

```bash
uv run pytest tests/api/admin/test_orgs_names.py --no-cov -q
```

Expected: same baseline. Org code passes `supports_metadata=False` (default) and ignores the new param.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/_names_shared.py src/api/admin/people_names.py \
        tests/api/admin/test_people_names_visibility.py
git commit -m "#123 feat: extend names router with visibility + expanded name_type for persons"
```

---

## Task 2: Templates — visibility select + expanded name_type + visibility badge

**Files:**
- Modify: `src/templates/admin/people/partials/_name_form_row.html`
- Modify: `src/templates/admin/people/partials/_name_row.html`

- [ ] **Step 1: Add failing template tests**

In `tests/api/admin/test_people_names_visibility.py`, append render tests:

```python
async def test_form_row_renders_visibility_select(db, client):
    pid = await _seed_person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, visibility) VALUES ($1, $2, 'Foo', 'public')",
        nid, pid,
    )
    response = client.get(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=AUTH,
    )
    assert 'name="visibility"' in response.text
    for v in ('public', 'legal_only', 'hidden'):
        assert f'value="{v}"' in response.text


async def test_form_row_renders_expanded_name_type_options(db, client):
    pid = await _seed_person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name) VALUES ($1, $2, 'Foo')",
        nid, pid,
    )
    response = client.get(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=AUTH,
    )
    for t in ('legal', 'preferred', 'alias', 'former', 'initials',
              'maiden', 'religious', 'stage', 'deadname',
              'reading', 'romanization', 'mrz'):
        assert f'value="{t}"' in response.text


async def test_read_row_shows_visibility_badge_when_legal_only(db, client):
    pid = await _seed_person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, visibility) VALUES ($1, $2, 'Foo', 'legal_only')",
        nid, pid,
    )
    response = client.get(
        f"/admin/people/{pid}/names/{nid}/read-row/",
        headers=AUTH,
    )
    assert "legal-only" in response.text.lower()
```

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Update `_name_form_row.html`**

Expand the existing `<select name="name_type">` loop to include all 12 values. Add a visibility select after the name_type select:

```jinja
<div class="form-group" style="margin-bottom:0">
  <select name="visibility">
    {% for v in ('public', 'legal_only', 'hidden') %}
    <option value="{{ v }}"{% if n and n.visibility == v %} selected{% endif %}>{{ v }}</option>
    {% endfor %}
  </select>
</div>
```

- [ ] **Step 4: Update `_name_row.html`**

Add visibility badge next to the name when not `'public'`:

```jinja
<td>
  {{ n.name }}
  {% if n.visibility and n.visibility != 'public' %}
    <span class="badge badge--warning" style="margin-left:var(--space-2)">{{ n.visibility|replace('_', '-') }}</span>
  {% endif %}
</td>
```

- [ ] **Step 5: Tests pass.**

- [ ] **Step 6: Smoke test**

Open `https://power-map.exe.xyz:8001/admin/people/<pid>/`, click "Edit" on a name, confirm visibility dropdown is present. Save with `legal_only`. Confirm badge renders. Reload — badge persists.

- [ ] **Step 7: Commit**

```bash
git add src/templates/admin/people/partials/_name_form_row.html \
        src/templates/admin/people/partials/_name_row.html \
        tests/api/admin/test_people_names_visibility.py
git commit -m "#123 feat: visibility select + expanded name_type + badge in person-name templates"
```

---

## Task 3: Person-detail toggle for legal/historical names

**Files:**
- Modify: `src/api/admin/people.py`
- Modify: `src/templates/admin/people/detail.html`

- [ ] **Step 1: Add failing tests**

```python
async def test_person_detail_excludes_legal_only_by_default(db, client):
    pid = await _seed_person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, 'Public', 'legal', TRUE, 'public')",
        generate_id(), pid,
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, 'Hidden Old', 'former', 'legal_only')",
        generate_id(), pid,
    )
    response = client.get(f"/admin/people/{pid}/", headers=AUTH)
    assert "Public" in response.text
    assert "Hidden Old" not in response.text


async def test_person_detail_shows_legal_only_with_toggle(db, client):
    pid = await _seed_person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical, visibility)"
        " VALUES ($1, $2, 'Public', 'legal', TRUE, 'public')",
        generate_id(), pid,
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
        " VALUES ($1, $2, 'Hidden Old', 'former', 'legal_only')",
        generate_id(), pid,
    )
    response = client.get(f"/admin/people/{pid}/?show_historical=1", headers=AUTH)
    assert "Public" in response.text
    assert "Hidden Old" in response.text


async def test_person_detail_renders_disclosure_toggle(db, client):
    pid = await _seed_person(db)
    response = client.get(f"/admin/people/{pid}/", headers=AUTH)
    assert "Show legal/historical names" in response.text
```

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Update `people.py` person-detail handler**

Read `show_historical: bool = Query(False)`. Branch the names query:

```python
if show_historical:
    names = await db.fetch(
        "SELECT * FROM person_names WHERE person_id = $1"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
else:
    names = await db.fetch(
        "SELECT * FROM person_names"
        f" WHERE person_id = $1 AND {visible_names_filter()}"
        " ORDER BY is_canonical DESC, name_type, name",
        person_id,
    )
```

The `visible_names_filter()` helper from `src.core.db` returns `"visibility = 'public'"`. The lint allow-list already covers `people.py`.

Pass `show_historical` to the template context.

- [ ] **Step 4: Update `detail.html`**

Add a toggle anchor near the names table heading:

```jinja
<div class="names-toggle" style="display:flex;align-items:center;gap:var(--space-2)">
  <h3>Names</h3>
  {% if show_historical %}
    <a href="?" class="btn btn--sm btn--secondary">Hide legal/historical names</a>
  {% else %}
    <a href="?show_historical=1" class="btn btn--sm btn--secondary">Show legal/historical names</a>
  {% endif %}
</div>
```

- [ ] **Step 5: Tests pass.**

- [ ] **Step 6: Smoke test**

Create a `legal_only` row via the form. Confirm it's hidden by default. Click toggle — row appears. Toggle off — row hidden again. Navigate away + back — toggle resets to off.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/people.py src/templates/admin/people/detail.html \
        tests/api/admin/test_people_names_visibility.py
git commit -m "#123 feat: 'Show legal/historical names' toggle on person detail"
```

---

## Task 4: Deadname confirmation dialog

**Files:**
- Create: `src/static/admin/person-name-deadname-confirm.js`
- Create: `tests/js/person-name-deadname-confirm.test.js`
- Modify: `src/templates/admin/people/detail.html` — `<script src="...">` include
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — add `data-deadname-confirm` attribute

- [ ] **Step 1: Add failing JS test**

```javascript
// tests/js/person-name-deadname-confirm.test.js
import { describe, it, expect, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';

const SCRIPT_PATH = path.resolve(
  __dirname, '../../src/static/admin/person-name-deadname-confirm.js'
);

describe('person-name-deadname-confirm', () => {
  let dom, window, document;

  beforeEach(() => {
    dom = new JSDOM(`
      <!DOCTYPE html>
      <html><body>
        <select name="name_type" data-deadname-confirm>
          <option value="legal" selected>legal</option>
          <option value="deadname">deadname</option>
          <option value="alias">alias</option>
        </select>
      </body></html>
    `, { runScripts: 'outside-only' });
    window = dom.window;
    document = window.document;
    const code = fs.readFileSync(SCRIPT_PATH, 'utf8');
    window.eval(code);
  });

  it('shows confirm() when changing to deadname', () => {
    const select = document.querySelector('select[name="name_type"]');
    window.confirm = vi.fn().mockReturnValue(true);
    select.value = 'deadname';
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(window.confirm.mock.calls[0][0]).toMatch(/deadname/i);
    expect(select.value).toBe('deadname');  // confirmed → kept
  });

  it('reverts when confirm() returns false', () => {
    const select = document.querySelector('select[name="name_type"]');
    window.confirm = vi.fn().mockReturnValue(false);
    select.value = 'deadname';
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
    expect(select.value).toBe('legal');  // reverted to previous
  });

  it('does not confirm for other name_type changes', () => {
    const select = document.querySelector('select[name="name_type"]');
    window.confirm = vi.fn();
    select.value = 'alias';
    select.dispatchEvent(new window.Event('change', { bubbles: true }));
    expect(window.confirm).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — confirm fail (script doesn't exist).**

```bash
npm test
```

- [ ] **Step 3: Implement the script**

```javascript
// src/static/admin/person-name-deadname-confirm.js
(function() {
  'use strict';

  const DEADNAME_MESSAGE =
    'Marking this as a deadname will hide it from public listings ' +
    '(visibility will be set to legal_only). Confirm?';

  function attach(select) {
    let previousValue = select.value;
    select.addEventListener('change', (e) => {
      if (e.target.value !== 'deadname') {
        previousValue = e.target.value;
        return;
      }
      if (window.confirm(DEADNAME_MESSAGE)) {
        previousValue = 'deadname';
      } else {
        select.value = previousValue;
      }
    });
  }

  document.querySelectorAll('select[data-deadname-confirm]').forEach(attach);

  // Re-attach after HTMX swaps (form-row reloaded after Edit click).
  document.body.addEventListener('htmx:afterSwap', (evt) => {
    evt.detail.target
      .querySelectorAll('select[data-deadname-confirm]')
      .forEach(attach);
  });
})();
```

- [ ] **Step 4: Wire it into the templates**

Add to `_name_form_row.html`'s name_type select:

```jinja
<select name="name_type" data-deadname-confirm>
```

Add to `detail.html` (near the other admin scripts):

```html
<script src="/static/admin/person-name-deadname-confirm.js" defer></script>
```

- [ ] **Step 5: JS tests pass.**

```bash
npm test -- person-name-deadname-confirm
```

- [ ] **Step 6: Smoke test**

In the browser, edit a name, change name_type to "deadname". Confirm dialog. Cancel — select reverts to previous. OK — select holds. Submit — server-side trigger sets visibility to legal_only.

- [ ] **Step 7: Commit**

```bash
git add src/static/admin/person-name-deadname-confirm.js \
        tests/js/person-name-deadname-confirm.test.js \
        src/templates/admin/people/partials/_name_form_row.html \
        src/templates/admin/people/detail.html
git commit -m "#123 feat: deadname confirmation dialog when admin changes name_type"
```

---

## Task 5: End-to-end smoke + final regression sweep

- [ ] **Manual flow on dev server**

1. Visit a person with one canonical legal name. Confirm visibility badge does NOT appear (default `'public'`).
2. Click Edit. Change name_type to `deadname`. Confirm dialog appears. OK. Save. Visibility now `legal_only`. The row vanishes from the table (excluded by default).
3. Click "Show legal/historical names". Row reappears with `legal-only` badge.
4. Click "Hide legal/historical names". Row vanishes.
5. Navigate to person list. Confirm the canonical name no longer renders (because the only canonical row is now `legal_only`, the view returns NULL display_name → row shows '(unnamed)' or similar — verify the existing fallback handles this gracefully).
6. Add a NEW canonical legal name with `visibility='public'`. Confirm person list now shows it.
7. Reload person-detail with `?show_historical=1` in the URL. Confirm both rows render.

- [ ] **Run full suite + JS + ruff**

```bash
uv run pytest --no-cov -q
npm test
uv run ruff check src/ tests/
```

Expected: same baseline pass count + Phase 2a tests; the pre-existing address-normalizer failure may persist; ruff clean.

- [ ] **Commit any final adjustments + push**

If smoke test reveals UI rough edges, commit minor follow-ups in this same branch before opening the PR.

---

## Done Criteria

- [ ] All 4 tasks committed on `feat/123-phase-2-admin-ui`.
- [ ] Backend tests cover: visibility round-trip, deadname coercion via the form, expanded name_type accepted, toggle filtering.
- [ ] JS tests cover: deadname confirm shown / accepted / cancelled / non-deadname-skipped.
- [ ] Manual smoke test on the dev server passes the 7-step flow above.
- [ ] No regressions in `tests/api/admin/test_orgs_names.py` (org-side names unaffected by `supports_metadata=False` default).

## Out of Scope (Phase 2b/2c/2d)

- Locale, script, sort_as fields (Phase 2b).
- `reading_of_id` linkage UX (Phase 2c).
- Structured parts editor (Phase 2d).
- Public API model changes (Phase 3).
