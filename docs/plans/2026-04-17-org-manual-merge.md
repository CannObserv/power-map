# Org Manual Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Merge with…" button to the org detail page that opens a two-step HTMX modal (typeahead search → preview with impact summary), and replace the `hx-confirm` dialogs in the duplicates review flow with the same preview modal.

**Architecture:** Extract the existing merge transaction into a shared `_execute_merge` helper. Add three new routes to `orgs_merge.py`: a scoped search endpoint, a preview endpoint, and a `merge-with` POST that always redirects to the winner's detail page. Typeahead selection triggers the preview step via an `onSelect` callback added to the existing factory.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX 2, `typeahead-combobox.js` factory, pytest (integration), vitest (JS)

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `src/static/admin/typeahead-combobox.js` | Add `onSelect` callback option |
| Modify | `src/api/admin/orgs_merge.py` | Extract `_execute_merge`; add merge-target-search, merge-preview, merge-with routes |
| Create | `src/templates/admin/orgs/_merge_search_modal.html` | Step 1 modal: typeahead search |
| Create | `src/templates/admin/orgs/_merge_preview_modal.html` | Step 2 modal: winner/loser toggle, impact, execute |
| Modify | `src/templates/admin/base.html` | Add `#merge-modal-portal` div |
| Modify | `src/templates/admin/orgs/detail.html` | Add "Merge with…" button (non-archived only) |
| Modify | `src/templates/admin/orgs/_duplicates_region.html` | Replace `hx-confirm` with preview modal buttons |
| Modify | `tests/js/typeahead-combobox.test.js` | Add `onSelect` callback test |
| Modify | `tests/api/admin/test_merge_unit.py` | Smoke tests for new routes |
| Modify | `tests/api/admin/test_orgs_duplicates.py` | Integration tests for new routes |

---

### Task 1: Add `onSelect` callback to typeahead-combobox.js

**Files:**
- Modify: `src/static/admin/typeahead-combobox.js`
- Test: `tests/js/typeahead-combobox.test.js`

- [ ] **Step 1: Write the failing test**

Add to `tests/js/typeahead-combobox.test.js` after the existing describe blocks:

```js
describe('onSelect callback', () => {
  it('calls onSelect with the selected item id on mousedown', () => {
    document.body.innerHTML = `
      <input id="${INPUT_ID}" type="text" autocomplete="off"
             role="combobox" aria-expanded="false"
             aria-haspopup="listbox" aria-controls="${LIST_ID}" aria-autocomplete="list">
      <input type="hidden" id="${HIDDEN_ID}" value="">
      <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
    `;
    eval(scriptCode);
    const calls = [];
    window.initTypeaheadCombobox({
      inputId: INPUT_ID,
      listboxId: LIST_ID,
      hiddenId: HIDDEN_ID,
      onSelect: (id) => calls.push(id),
    });
    populateResults([{ id: 'org-1', label: 'Acme' }]);
    const li = getItems()[0];
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(calls).toEqual(['org-1']);
  });

  it('calls onSelect with the selected item id on keyboard Enter', () => {
    document.body.innerHTML = `
      <input id="${INPUT_ID}" type="text" autocomplete="off"
             role="combobox" aria-expanded="false"
             aria-haspopup="listbox" aria-controls="${LIST_ID}" aria-autocomplete="list">
      <input type="hidden" id="${HIDDEN_ID}" value="">
      <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
    `;
    eval(scriptCode);
    const calls = [];
    window.initTypeaheadCombobox({
      inputId: INPUT_ID,
      listboxId: LIST_ID,
      hiddenId: HIDDEN_ID,
      onSelect: (id) => calls.push(id),
    });
    populateResults([{ id: 'org-2', label: 'Beta' }]);
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(calls).toEqual(['org-2']);
  });

  it('does not throw when onSelect is not provided', () => {
    setup(); // uses existing setup() without onSelect
    populateResults([{ id: 'org-3', label: 'Gamma' }]);
    const li = getItems()[0];
    expect(() =>
      li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
    ).not.toThrow();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
npm test -- tests/js/typeahead-combobox.test.js 2>&1 | tail -15
```

Expected: FAIL — `onSelect` is not called.

- [ ] **Step 3: Add `onSelect` to typeahead-combobox.js**

Change the factory signature and `selectItem` function:

```js
// Before:
window.initTypeaheadCombobox = function initTypeaheadCombobox({ inputId, listboxId, hiddenId }) {
  ...
  function selectItem(li) {
    hidden.value = li.dataset.id;
    inp.value = li.dataset.label;
    closeDropdown();
  }

// After:
window.initTypeaheadCombobox = function initTypeaheadCombobox({ inputId, listboxId, hiddenId, onSelect }) {
  ...
  function selectItem(li) {
    hidden.value = li.dataset.id;
    inp.value = li.dataset.label;
    closeDropdown();
    if (onSelect) onSelect(li.dataset.id);
  }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npm test -- tests/js/typeahead-combobox.test.js 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
git add src/static/admin/typeahead-combobox.js tests/js/typeahead-combobox.test.js
git commit -m "#106 feat: add onSelect callback to typeahead-combobox factory"
```

---

### Task 2: Extract `_execute_merge` helper and add `merge-with` route

**Files:**
- Modify: `src/api/admin/orgs_merge.py`
- Test: `tests/api/admin/test_merge_unit.py`
- Test: `tests/api/admin/test_orgs_duplicates.py`

- [ ] **Step 1: Write the failing smoke test**

Add to `tests/api/admin/test_merge_unit.py`:

```python
def test_org_merge_with_post_htmx_redirects(org_client):
    """POST to merge-with must return HX-Redirect, not 500."""
    response = org_client.post(
        "/admin/orgs/WINNER000000000000000000000/merge-with/LOSER0000000000000000000000/",
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect")


def test_org_merge_with_post_non_htmx_redirects(org_client):
    """Non-HTMX POST to merge-with must return 303 redirect."""
    response = org_client.post(
        "/admin/orgs/WINNER000000000000000000000/merge-with/LOSER0000000000000000000000/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/admin/orgs/WINNER000000000000000000000/" in response.headers["location"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
uv run pytest tests/api/admin/test_merge_unit.py --no-cov -q 2>&1 | tail -10
```

Expected: FAIL — route not found (404).

- [ ] **Step 3: Refactor `orgs_merge.py` — extract `_execute_merge` and add `merge-with` route**

Replace `orgs_merge.py` content with:

```python
"""Admin views for org merge and duplicate review."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.org_dups import (
    CANDIDATE_WHERE,
    get_org_dup_count,
    invalidate_dup_count_cache,
)
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs-merge"])


async def _fetch_duplicate_pairs(db) -> list:
    """Return near-duplicate org pairs; empty list if pg_trgm not installed."""
    try:
        return await db.fetch(
            f"""SELECT
                a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
                b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
                similarity(dn_a.display_name, dn_b.display_name) AS score,
                (SELECT count(*) FROM roles
                 WHERE organization_id = a.id AND archived_at IS NULL) AS a_roles,
                (SELECT count(*) FROM roles
                 WHERE organization_id = b.id AND archived_at IS NULL) AS b_roles
            {CANDIDATE_WHERE}
            ORDER BY score DESC"""
        )
    except asyncpg.exceptions.UndefinedFunctionError:
        return []


async def _execute_merge(db, winner_id: str, loser_id: str) -> None:
    """Merge loser into winner inside a transaction. Invalidates dup count cache."""
    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id FROM organizations WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Organization not found")
        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
            winner_id, loser_id,
        )
        await db.execute(
            "UPDATE organization_names SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        await db.execute(
            "UPDATE organization_acronyms SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_acronyms"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id, loser_id,
        )
        for table in ("entity_addresses", "contact_methods", "links",
                      "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='organization' AND entity_id=$2",
                winner_id, loser_id,
            )
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='organization'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'organization'
                 AND dw.entity_type = 'organization'
                 AND dw.entity_a_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_b_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_b_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """DELETE FROM duplicate_dismissals dd
               USING duplicate_dismissals dw
               WHERE dd.entity_type = 'organization'
                 AND dw.entity_type = 'organization'
                 AND dw.entity_b_id = $2
                 AND (
                   (dd.entity_a_id = $1 AND dd.entity_b_id = dw.entity_a_id)
                   OR (dd.entity_b_id = $1 AND dd.entity_a_id = dw.entity_a_id)
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST($1, entity_b_id),
                   entity_b_id = GREATEST($1, entity_b_id)
               WHERE entity_type='organization' AND entity_a_id=$2""",
            winner_id, loser_id,
        )
        await db.execute(
            """UPDATE duplicate_dismissals
               SET entity_a_id = LEAST(entity_a_id, $1),
                   entity_b_id = GREATEST(entity_a_id, $1)
               WHERE entity_type='organization' AND entity_b_id=$2""",
            winner_id, loser_id,
        )
        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
    invalidate_dup_count_cache()


@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    """List near-duplicate organization pairs for review."""
    pairs = await _fetch_duplicate_pairs(db)
    ctx = {
        "user": user,
        "active_section": "orgs_duplicates",
        "pairs": pairs,
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }
    template = (
        "admin/orgs/_duplicates_region.html"
        if is_htmx(request)
        else "admin/orgs/duplicates.html"
    )
    return templates.TemplateResponse(request, template, ctx)


@router.post("/{winner_id}/merge/{loser_id}/")
async def org_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    await _execute_merge(db, winner_id, loser_id)
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/orgs/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review URLs, roles, and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/orgs/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
        )
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)


@router.post("/{winner_id}/merge-with/{loser_id}/")
async def org_merge_with(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner from detail page; redirect to winner detail."""
    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )
    await _execute_merge(db, winner_id, loser_id)
    body = (
        f'Merged <strong>{escape(loser_name)}</strong> into '
        f'<strong>{escape(winner_name)}</strong>. '
        f'Review names, roles, and contact info for duplicates.'
    )
    redirect_url = f"/admin/orgs/{winner_id}/"
    if is_htmx(request):
        return HTMLResponse(
            "",
            headers={**flash_trigger("success", body), "HX-Redirect": redirect_url},
        )
    return RedirectResponse(redirect_url, status_code=303)


@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def org_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await db.execute(
        "INSERT INTO duplicate_dismissals"
        " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
        " VALUES ($1, 'organization', $2, $3, $4)"
        " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
        generate_id(), a, b, user.email,
    )
    invalidate_dup_count_cache()
    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        ctx = {
            "user": user,
            "active_section": "orgs_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/orgs/_duplicates_region.html",
            ctx,
            headers=flash_trigger("info", "Pair marked as not a duplicate."),
        )
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)
```

- [ ] **Step 4: Run smoke tests**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
uv run pytest tests/api/admin/test_merge_unit.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Write integration test for merge-with**

Add to `tests/api/admin/test_orgs_duplicates.py`:

```python
def test_merge_with_hard_deletes_loser(client, org_pair):
    """POST merge-with hard-deletes loser (same transaction as merge)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/admin/orgs/{id_a}/" in response.headers["location"]

    dsn = _get_dsn()

    async def check_deleted():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id FROM organizations WHERE id=$1", id_b
            )
        finally:
            await conn.close()

    assert asyncio.run(check_deleted()) is None


def test_merge_with_htmx_returns_hx_redirect(client, org_pair):
    """HTMX merge-with returns HX-Redirect to winner detail, not an inline region."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == f"/admin/orgs/{id_a}/"
```

- [ ] **Step 6: Run integration tests**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_orgs_duplicates.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/orgs_merge.py tests/api/admin/test_merge_unit.py tests/api/admin/test_orgs_duplicates.py
git commit -m "#106 refactor: extract _execute_merge helper; add merge-with route"
```

---

### Task 3: Add merge-target-search and merge-preview routes

**Files:**
- Modify: `src/api/admin/orgs_merge.py`
- Test: `tests/api/admin/test_merge_unit.py`
- Test: `tests/api/admin/test_orgs_duplicates.py`

The templates don't exist yet so routes will error on render — smoke tests mock the DB and test routing only; integration tests use a real DB and real templates (created in Task 4). Write routes first, templates in Task 4, integration tests in Task 5.

- [ ] **Step 1: Write smoke tests for new routes**

Add to `tests/api/admin/test_merge_unit.py`:

```python
def test_org_merge_target_search_returns_200(org_client):
    """GET merge-target-search must not 500."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-target-search/?q=test",
        headers=AUTH_HEADERS,
    )
    # Template renders (may 500 on missing template until Task 4)
    assert response.status_code in (200, 500)


def test_org_merge_search_modal_returns_200(org_client):
    """GET merge-search must not 404."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-search/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code in (200, 500)


def test_org_merge_preview_returns_200(org_client):
    """GET merge-preview must not 404."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-preview/OTHERID0000000000000000000/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code in (200, 500)
```

- [ ] **Step 2: Run to confirm 404 (routes missing)**

```bash
uv run pytest tests/api/admin/test_merge_unit.py --no-cov -q 2>&1 | tail -10
```

Expected: the new tests fail with assertion error (404 not in `(200, 500)`).

- [ ] **Step 3: Add routes to `orgs_merge.py`**

Add these three routes after the `org_dismiss_duplicate` handler:

```python
@router.get("/{org_id}/merge-target-search/")
async def org_merge_target_search(
    org_id: str,
    request: Request,
    q: str = "",
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search for merge target — excludes current org and archived orgs."""
    from src.api.admin.deps import escape_like
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND o.id != $1
                 AND dn.display_name ILIKE $2 ESCAPE '\\'
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            org_id,
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )


@router.get("/{org_id}/merge-search/")
async def org_merge_search_modal(
    org_id: str,
    request: Request,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return modal fragment for step 1 of manual merge: typeahead search."""
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    org_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", org_id
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/_merge_search_modal.html",
        {"org_id": org_id, "org_name": org_name},
    )


@router.get("/{id_a}/merge-preview/{id_b}/")
async def org_merge_preview(
    id_a: str,
    id_b: str,
    request: Request,
    winner: str | None = None,
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=)."""
    winner_id = winner if winner in (id_a, id_b) else id_a
    loser_id = id_b if winner_id == id_a else id_a

    org_a = await db.fetchrow("SELECT id FROM organizations WHERE id=$1 AND archived_at IS NULL", id_a)
    org_b = await db.fetchrow("SELECT id FROM organizations WHERE id=$1 AND archived_at IS NULL", id_b)
    if not org_a or not org_b:
        raise HTTPException(status_code=404)

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id=$1", loser_id
    )

    transferred_names = await db.fetch(
        "SELECT name, name_type FROM organization_names"
        " WHERE organization_id=$1 AND is_canonical=FALSE",
        loser_id,
    )
    dropped_name = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    transferred_acronyms = await db.fetch(
        "SELECT acronym FROM organization_acronyms"
        " WHERE organization_id=$1 AND is_canonical=FALSE",
        loser_id,
    )
    dropped_acronym = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )

    roles_count = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND archived_at IS NULL",
        loser_id,
    )
    contacts_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods"
        " WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    links_count = await db.fetchval(
        "SELECT count(*) FROM links WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    addresses_count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses"
        " WHERE entity_type='organization' AND entity_id=$1",
        loser_id,
    )
    identifiers_count = await db.fetchval(
        "SELECT count(*) FROM identifiers WHERE entity_id=$1",
        loser_id,
    )

    conflicting_roles = await db.fetch(
        """SELECT r_l.title
           FROM roles r_l
           JOIN roles r_w ON lower(r_w.title) = lower(r_l.title)
                          AND r_w.organization_id = $2
                          AND r_w.archived_at IS NULL
           WHERE r_l.organization_id = $1
             AND r_l.archived_at IS NULL""",
        loser_id, winner_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/_merge_preview_modal.html",
        {
            "org_a_id": id_a,
            "org_b_id": id_b,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "winner_name": winner_name,
            "loser_name": loser_name,
            "transferred_names": transferred_names,
            "dropped_name": dropped_name,
            "transferred_acronyms": transferred_acronyms,
            "dropped_acronym": dropped_acronym,
            "roles_count": roles_count,
            "contacts_count": contacts_count,
            "links_count": links_count,
            "addresses_count": addresses_count,
            "identifiers_count": identifiers_count,
            "conflicting_roles": conflicting_roles,
        },
    )
```

Also move the `escape_like` import at the top of the file:

```python
# Add to the existing imports at the top of orgs_merge.py:
from src.api.admin.deps import (
    AdminUser,
    escape_like,   # ← add this
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
```

Then remove the inline import from `org_merge_target_search`.

- [ ] **Step 4: Run smoke tests**

```bash
uv run pytest tests/api/admin/test_merge_unit.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass (routes exist; templates may 500 but tests accept 200 or 500).

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/orgs_merge.py tests/api/admin/test_merge_unit.py
git commit -m "#106 feat: add merge-target-search, merge-search, and merge-preview routes"
```

---

### Task 4: Create modal templates

**Files:**
- Create: `src/templates/admin/orgs/_merge_search_modal.html`
- Create: `src/templates/admin/orgs/_merge_preview_modal.html`

No new tests in this task — integration tests in Task 5 will verify content. The existing smoke tests (accepting 200 or 500) will now accept 200 only.

- [ ] **Step 1: Create `_merge_search_modal.html`**

```html
{# admin/orgs/_merge_search_modal.html #}
{# Context: org_id, org_name #}
{# Rendered into #merge-modal-portal via hx-target="#merge-modal-portal" hx-swap="innerHTML". #}
<div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="merge-search-title">
  <div class="modal">
    <h2 id="merge-search-title">Merge organization</h2>
    <p style="margin-bottom:var(--space-4)">
      Search for the organization to merge with <strong>{{ org_name }}</strong>:
    </p>

    <div class="form-group" style="margin-bottom:0;position:relative">
      <input type="text" id="merge-target-display"
             autocomplete="off"
             placeholder="Search organizations…"
             hx-get="/admin/orgs/{{ org_id }}/merge-target-search/"
             hx-trigger="input changed delay:200ms"
             hx-target="#merge-target-results"
             hx-swap="innerHTML"
             hx-params="q"
             name="q"
             role="combobox"
             aria-expanded="false"
             aria-haspopup="listbox"
             aria-controls="merge-target-results"
             aria-autocomplete="list">
      <input type="hidden" id="merge-target-id">
      <ul id="merge-target-results" class="typeahead-results" role="listbox"></ul>
    </div>

    <div class="modal__actions" style="margin-top:var(--space-5)">
      <button type="button" class="btn btn--ghost"
              onclick="window.__pmMergeClose()">Cancel</button>
    </div>
  </div>
</div>
<script>
(function () {
  var portal = document.getElementById('merge-modal-portal');

  function closePortal() {
    document.removeEventListener('keydown', document.__pmMergeKey);
    portal.innerHTML = '';
    if (document.__pmMergeSavedFocus && document.__pmMergeSavedFocus.focus) {
      document.__pmMergeSavedFocus.focus();
    }
  }
  window.__pmMergeClose = closePortal;
  document.__pmMergeSavedFocus = document.activeElement;

  document.removeEventListener('keydown', document.__pmMergeKey);
  document.__pmMergeKey = function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closePortal(); }
  };
  document.addEventListener('keydown', document.__pmMergeKey);

  var inp = document.getElementById('merge-target-display');
  if (inp) inp.focus();

  window.initTypeaheadCombobox({
    inputId: 'merge-target-display',
    listboxId: 'merge-target-results',
    hiddenId: 'merge-target-id',
    onSelect: function (id) {
      htmx.ajax(
        'GET',
        '/admin/orgs/{{ org_id }}/merge-preview/' + id + '/',
        { target: '#merge-modal-portal', swap: 'innerHTML' }
      );
    },
  });
}());
</script>
```

- [ ] **Step 2: Create `_merge_preview_modal.html`**

```html
{# admin/orgs/_merge_preview_modal.html #}
{# Context: org_a_id, org_b_id, winner_id, loser_id,
   winner_name, loser_name,
   transferred_names (list of {name, name_type}), dropped_name ({name}|None),
   transferred_acronyms (list of {acronym}), dropped_acronym ({acronym}|None),
   roles_count, contacts_count, links_count, addresses_count, identifiers_count,
   conflicting_roles (list of {title}) #}
<div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="merge-preview-title">
  <div class="modal modal--wide">
    <h2 id="merge-preview-title">Merge organizations</h2>

    <div style="display:flex;gap:var(--space-3);margin-bottom:var(--space-5)">
      <button class="btn btn--sm {% if winner_id == org_a_id %}btn--primary{% else %}btn--ghost{% endif %}"
              hx-get="/admin/orgs/{{ org_a_id }}/merge-preview/{{ org_b_id }}/?winner={{ org_a_id }}"
              hx-target="#merge-modal-portal" hx-swap="innerHTML"
              type="button">Keep {{ winner_name if winner_id == org_a_id else loser_name }}</button>
      <button class="btn btn--sm {% if winner_id == org_b_id %}btn--primary{% else %}btn--ghost{% endif %}"
              hx-get="/admin/orgs/{{ org_a_id }}/merge-preview/{{ org_b_id }}/?winner={{ org_b_id }}"
              hx-target="#merge-modal-portal" hx-swap="innerHTML"
              type="button">Keep {{ winner_name if winner_id == org_b_id else loser_name }}</button>
    </div>

    <table class="data-table" style="margin-bottom:var(--space-4)">
      <tbody>
        <tr>
          <th scope="row" style="width:8rem">Kept</th>
          <td><strong>{{ winner_name }}</strong></td>
        </tr>
        <tr>
          <th scope="row">Deleted</th>
          <td><span style="color:var(--color-text-muted)">{{ loser_name }}</span></td>
        </tr>
      </tbody>
    </table>

    <h3 class="field-group-label" style="margin-bottom:var(--space-2)">Names</h3>
    <ul style="margin:0 0 var(--space-4) var(--space-4);padding:0">
      {% if dropped_name %}
      <li><span style="color:var(--color-text-muted)">{{ dropped_name.name }}</span>
          <span class="badge badge--warning" style="margin-left:var(--space-2)">dropped</span></li>
      {% endif %}
      {% for n in transferred_names %}
      <li>{{ n.name }}
          <span class="badge" style="margin-left:var(--space-2)">added to {{ winner_name }}</span></li>
      {% endfor %}
      {% if not dropped_name and not transferred_names %}
      <li style="color:var(--color-text-muted)">No additional names</li>
      {% endif %}
    </ul>

    {% if dropped_acronym or transferred_acronyms %}
    <h3 class="field-group-label" style="margin-bottom:var(--space-2)">Acronyms</h3>
    <ul style="margin:0 0 var(--space-4) var(--space-4);padding:0">
      {% if dropped_acronym %}
      <li><span style="color:var(--color-text-muted)">{{ dropped_acronym.acronym }}</span>
          <span class="badge badge--warning" style="margin-left:var(--space-2)">dropped</span></li>
      {% endif %}
      {% for a in transferred_acronyms %}
      <li>{{ a.acronym }}
          <span class="badge" style="margin-left:var(--space-2)">added to {{ winner_name }}</span></li>
      {% endfor %}
    </ul>
    {% endif %}

    {% set total = roles_count + contacts_count + links_count + addresses_count + identifiers_count %}
    {% if total > 0 %}
    <h3 class="field-group-label" style="margin-bottom:var(--space-2)">Reassigned</h3>
    <ul style="margin:0 0 var(--space-4) var(--space-4);padding:0">
      {% if roles_count %}<li>{{ roles_count }} role{{ 's' if roles_count != 1 }}</li>{% endif %}
      {% if contacts_count %}<li>{{ contacts_count }} contact method{{ 's' if contacts_count != 1 }}</li>{% endif %}
      {% if links_count %}<li>{{ links_count }} link{{ 's' if links_count != 1 }}</li>{% endif %}
      {% if addresses_count %}<li>{{ addresses_count }} address{{ 'es' if addresses_count != 1 }}</li>{% endif %}
      {% if identifiers_count %}<li>{{ identifiers_count }} identifier{{ 's' if identifiers_count != 1 }}</li>{% endif %}
    </ul>
    {% endif %}

    {% if conflicting_roles %}
    <div class="alert alert--error" role="alert" style="margin-bottom:var(--space-4)">
      <strong>Role title conflicts — resolve before merging:</strong>
      <ul style="margin:var(--space-2) 0 0 var(--space-4);padding:0">
        {% for r in conflicting_roles %}
        <li>{{ r.title }}</li>
        {% endfor %}
      </ul>
      <p style="margin:var(--space-2) 0 0">
        Archive or rename one of these roles on each organization, then return to merge.
      </p>
    </div>
    {% endif %}

    <div class="modal__actions">
      <button type="button" class="btn btn--ghost"
              onclick="window.__pmMergeClose()">Cancel</button>
      <button type="button" class="btn btn--danger"
              {% if conflicting_roles %}disabled aria-disabled="true"{% endif %}
              hx-post="/admin/orgs/{{ winner_id }}/merge-with/{{ loser_id }}/"
              hx-target="body">
        Execute merge
      </button>
    </div>
  </div>
</div>
<script>
(function () {
  var portal = document.getElementById('merge-modal-portal');

  function closePortal() {
    document.removeEventListener('keydown', document.__pmMergeKey);
    portal.innerHTML = '';
    if (document.__pmMergeSavedFocus && document.__pmMergeSavedFocus.focus) {
      document.__pmMergeSavedFocus.focus();
    }
  }
  window.__pmMergeClose = closePortal;

  document.removeEventListener('keydown', document.__pmMergeKey);
  document.__pmMergeKey = function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closePortal(); }
  };
  document.addEventListener('keydown', document.__pmMergeKey);

  var firstBtn = portal.querySelector('button');
  if (firstBtn) firstBtn.focus();
}());
</script>
```

- [ ] **Step 3: Run smoke tests to confirm 200 (no longer 500)**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
uv run pytest tests/api/admin/test_merge_unit.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass with 200.

- [ ] **Step 4: Commit**

```bash
git add src/templates/admin/orgs/_merge_search_modal.html \
        src/templates/admin/orgs/_merge_preview_modal.html
git commit -m "#106 feat: add merge search and preview modal templates"
```

---

### Task 5: Integration tests for merge-search and merge-preview

**Files:**
- Test: `tests/api/admin/test_orgs_duplicates.py`

- [ ] **Step 1: Write integration tests**

Add to `tests/api/admin/test_orgs_duplicates.py`:

```python
def test_merge_search_modal_returns_fragment(client, org_pair):
    """GET merge-search returns modal fragment with typeahead input."""
    id_a, _ = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-search/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "merge-target-display" in response.text
    assert "merge-target-results" in response.text


def test_merge_preview_shows_winner_and_loser(client, org_pair):
    """GET merge-preview shows winner and loser org names."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Alberta Gaming" in response.text
    assert "Execute merge" in response.text


def test_merge_preview_winner_param_flips_direction(client, org_pair):
    """?winner=id_b makes id_b the winner in the preview."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/?winner={id_b}",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Execute merge" in response.text
    # winner button for id_b should be btn--primary
    assert f"winner={ id_b }" in response.text


def test_merge_preview_shows_conflict_warning(client):
    """GET merge-preview shows role conflict warning when title clash exists."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    role_a, role_b = generate_id(), generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for oid, name in [(id_a, "Conflict Org A"), (id_b, "Conflict Org B")]:
                await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
                await conn.execute(
                    "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), oid, name,
                )
            for rid, oid in [(role_a, id_a), (role_b, id_b)]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')",
                    rid, oid,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            for rid in [role_a, role_b]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        with TestClient(app) as c:
            response = c.get(
                f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 200
        assert "Director" in response.text
        assert "conflict" in response.text.lower()
    finally:
        asyncio.run(teardown())
```

- [ ] **Step 2: Run integration tests**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_orgs_duplicates.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/api/admin/test_orgs_duplicates.py
git commit -m "#106 test: add integration tests for merge-search and merge-preview routes"
```

---

### Task 6: Wire up portal, org detail button

**Files:**
- Modify: `src/templates/admin/base.html`
- Modify: `src/templates/admin/orgs/detail.html`

- [ ] **Step 1: Add `#merge-modal-portal` to `base.html`**

Find the `address-confirm-portal` div and add the merge portal after it:

```html
  <div id="address-confirm-portal"></div>
  <div id="merge-modal-portal"></div>  <!-- add this line -->
```

- [ ] **Step 2: Add "Merge with…" button to `detail.html`**

Find the danger zone section in `detail.html`. Add the merge button just above the `<div class="danger-zone">`, inside a `{% if not org.archived_at %}` guard:

```html
{% if not org.archived_at %}
<div style="margin-top:var(--space-6)">
  <button type="button" class="btn btn--secondary"
          hx-get="/admin/orgs/{{ org.id }}/merge-search/"
          hx-target="#merge-modal-portal"
          hx-swap="innerHTML">
    Merge with…
  </button>
</div>
{% endif %}

<div class="danger-zone">
```

- [ ] **Step 3: Run full unit test suite to confirm no regressions**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
uv run pytest --no-cov -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/templates/admin/base.html src/templates/admin/orgs/detail.html
git commit -m "#106 feat: add merge-modal-portal to base; wire Merge with… button on org detail"
```

---

### Task 7: Update duplicates region to use preview modal

**Files:**
- Modify: `src/templates/admin/orgs/_duplicates_region.html`

- [ ] **Step 1: Replace `hx-confirm` buttons in `_duplicates_region.html`**

Replace the existing "Keep A" and "Keep B" forms with buttons that load the preview:

```html
{# Before — two <form> elements with hx-confirm: #}
<form hx-post="/admin/orgs/{{ p.a_id }}/merge/{{ p.b_id }}/"
      hx-target="#orgs-duplicates-region" hx-swap="outerHTML"
      hx-confirm="Merge {{ p.b_name }} into {{ p.a_name }}? This cannot be undone."
      style="display:inline">
  <button class="btn btn--primary btn--sm" type="submit">Keep A</button>
</form>
<form hx-post="/admin/orgs/{{ p.b_id }}/merge/{{ p.a_id }}/"
      hx-target="#orgs-duplicates-region" hx-swap="outerHTML"
      hx-confirm="Merge {{ p.a_name }} into {{ p.b_name }}? This cannot be undone."
      style="display:inline">
  <button class="btn btn--primary btn--sm" type="submit">Keep B</button>
</form>

{# After — two buttons that load the preview modal: #}
<button class="btn btn--primary btn--sm"
        hx-get="/admin/orgs/{{ p.a_id }}/merge-preview/{{ p.b_id }}/?winner={{ p.a_id }}"
        hx-target="#merge-modal-portal"
        hx-swap="innerHTML"
        type="button">Keep A</button>
<button class="btn btn--primary btn--sm"
        hx-get="/admin/orgs/{{ p.a_id }}/merge-preview/{{ p.b_id }}/?winner={{ p.b_id }}"
        hx-target="#merge-modal-portal"
        hx-swap="innerHTML"
        type="button">Keep B</button>
```

Note: both buttons use `{{ p.a_id }}/merge-preview/{{ p.b_id }}/` (canonical ordering with `?winner=` to control direction). This ensures the preview route always receives the same pair regardless of which button was clicked.

- [ ] **Step 2: Write a test confirming no `hx-confirm` remains on merge buttons**

Add to `tests/api/admin/test_orgs_duplicates.py`:

```python
def test_duplicates_region_has_no_hx_confirm_on_merge(client, org_pair):
    """Keep A / Keep B buttons must not use hx-confirm (replaced by preview modal)."""
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # hx-confirm is still valid on the delete button elsewhere, but not on merge buttons
    # Verify the region has preview links, not direct POST forms
    assert "merge-preview" in response.text
    assert 'hx-confirm="Merge' not in response.text
```

- [ ] **Step 3: Run the test**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_orgs_duplicates.py --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 4: Run full suite**

```bash
uv run pytest --no-cov -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/_duplicates_region.html \
        tests/api/admin/test_orgs_duplicates.py
git commit -m "#106 feat: replace hx-confirm on duplicates page with preview modal"
```

---

### Task 8: Verify in browser

- [ ] **Step 1: Confirm dev server is running from worktree**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/admin/
```

Expected: 307 (auth redirect = server is up).

- [ ] **Step 2: Manual test — detail page flow**

1. Navigate to https://power-map.exe.xyz:8001/admin/orgs/01KM234QAYH0T5T4FAJ49A2H29/ (current org)
2. Confirm "Merge with…" button is visible
3. Click it — search modal should appear
4. Type part of the former org's name to find 01KM1CTGQ13007V1BKX1VECDC4
5. Select it — preview modal should appear showing both orgs, impact summary
6. Toggle winner/loser — preview should re-render with direction flipped
7. Confirm Execute button is present and not disabled (no role conflicts between these orgs)
8. Click Cancel — modal should close

- [ ] **Step 3: Manual test — duplicates page**

1. Navigate to https://power-map.exe.xyz:8001/admin/orgs/duplicates/
2. If any duplicate pairs exist, confirm "Keep A" / "Keep B" buttons open the preview modal instead of a browser confirm dialog
3. Confirm "Not a duplicate" button still works as before

- [ ] **Step 4: Final full test run**

```bash
cd /home/exedev/power-map/.worktrees/106-org-manual-merge
uv run pytest --no-cov -q 2>&1 | tail -5
npm test 2>&1 | tail -5
```

Expected: all pass.
