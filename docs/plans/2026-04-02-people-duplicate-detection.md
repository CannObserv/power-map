# People Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement people duplicate detection parallel to the org duplicate feature: a `v_person_display_names` DB view, a TTL-cached detection module, a dismissable notice on the People list, and a review screen.

**Architecture:** Mirror `org_dups.py` / `orgs.py` / org duplicate templates exactly. New module `people_dups.py` owns the cache and SQL. `people.py` grows three new routes (GET `/duplicates/`, POST `/{id_a}/dismiss-duplicate/{id_b}/`) and injects `person_dup_count` alongside the existing `org_dup_count` on every route. Two new templates plus edits to `list.html` and `base.html`.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Jinja2, HTMX, PostgreSQL with pg_trgm

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/core/schema.sql` | Add `v_person_display_names` view |
| Create | `src/api/admin/people_dups.py` | CANDIDATE_WHERE SQL, TTL cache, count/dep/invalidate |
| Modify | `src/api/admin/people.py` | Inject `person_dup_count`; add `_fetch_duplicate_pairs`, `/duplicates/`, dismiss route |
| Modify | `src/templates/admin/people/list.html` | Add dismissable dup notice above filter card |
| Create | `src/templates/admin/people/duplicates.html` | Full-page duplicates screen |
| Create | `src/templates/admin/people/_duplicates_region.html` | HTMX-swappable pairs table |
| Modify | `src/templates/admin/base.html` | Add People › Duplicates sidebar sublink with count badge |
| Create | `tests/api/admin/test_people_dups.py` | Unit tests for cache / count / dep |
| Create | `tests/api/admin/test_people_duplicates.py` | Integration tests for routes |

---

## Task 1: Add `v_person_display_names` to schema

**Files:**
- Modify: `src/core/schema.sql` (after line 148, after `uq_person_canonical_name` index)

- [ ] **Step 1: Write the failing test**

  File: `tests/api/admin/test_people_dups.py` (create new file)

  ```python
  """Unit tests for people-duplicate detection logic (cache, count, dep)."""

  from unittest.mock import AsyncMock, MagicMock

  import pytest

  # This import will fail until people_dups.py exists — that's the failing test.
  from src.api.admin.people_dups import (
      count_person_duplicates,
      get_person_dup_count,
      invalidate_dup_count_cache,
  )
  ```

  Run: `python -m pytest tests/api/admin/test_people_dups.py -v`
  Expected: **ImportError** — `people_dups` module does not exist.

- [ ] **Step 2: Add `v_person_display_names` view to `schema.sql`**

  Insert after the `uq_person_canonical_name` index block (after line 148, before the `-- Role =` comment):

  ```sql
  CREATE OR REPLACE VIEW v_person_display_names AS
  SELECT p.id AS person_id,
         n.name AS display_name
  FROM people p
  LEFT JOIN person_names n
      ON n.person_id = p.id AND n.is_canonical = TRUE
  ;
  ```

- [ ] **Step 3: Verify schema applies cleanly**

  ```bash
  python -m pytest tests/ -k "not integration" -v --tb=short -q
  ```
  Expected: all non-integration tests pass (schema change is additive, no breakage).

- [ ] **Step 4: Commit**

  ```bash
  git add src/core/schema.sql
  git commit -m "#53 feat: add v_person_display_names view to schema"
  ```

---

## Task 2: Create `people_dups.py` detection module

**Files:**
- Create: `src/api/admin/people_dups.py`
- Test: `tests/api/admin/test_people_dups.py`

- [ ] **Step 1: Implement `people_dups.py`**

  Create `src/api/admin/people_dups.py`:

  ```python
  """People-duplicate detection: SQL, TTL cache, and FastAPI dependency."""

  import time

  from fastapi import Depends

  from src.api.admin.deps import get_db

  CANDIDATE_WHERE = """
      FROM people a
      JOIN people b ON b.id > a.id
      JOIN v_person_display_names dn_a ON dn_a.person_id = a.id
      JOIN v_person_display_names dn_b ON dn_b.person_id = b.id
      WHERE a.archived_at IS NULL AND b.archived_at IS NULL
        AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
        AND NOT EXISTS (
            SELECT 1 FROM duplicate_dismissals
            WHERE entity_type = 'person'
              AND entity_a_id = a.id AND entity_b_id = b.id
        )
  """

  _DUP_COUNT_TTL = 300.0  # seconds
  _dup_count_cache: dict[str, int | float] = {"value": 0, "expires": 0.0}


  def invalidate_dup_count_cache() -> None:
      """Expire the cached duplicate count so the next request re-queries."""
      _dup_count_cache["expires"] = 0.0


  async def count_person_duplicates(db) -> int:
      """Return count of non-dismissed near-duplicate person pairs (TTL-cached, 5 min)."""
      now = time.monotonic()
      if now < _dup_count_cache["expires"]:
          return _dup_count_cache["value"]
      count = await db.fetchval(f"SELECT count(*) {CANDIDATE_WHERE}")
      _dup_count_cache["value"] = count
      _dup_count_cache["expires"] = now + _DUP_COUNT_TTL
      return count


  async def get_person_dup_count(db=Depends(get_db)) -> int:
      """FastAPI dependency: cached person duplicate count, defaults to 0 on error."""
      try:
          return await count_person_duplicates(db)
      except Exception:
          return 0
  ```

- [ ] **Step 2: Complete unit tests in `test_people_dups.py`**

  Replace the stub from Task 1 with the full test file:

  ```python
  """Unit tests for people-duplicate detection logic (cache, count, dep)."""

  from unittest.mock import AsyncMock, MagicMock

  import pytest

  from src.api.admin.people_dups import (
      count_person_duplicates,
      get_person_dup_count,
      invalidate_dup_count_cache,
  )


  def _make_db(fetchval_return: int) -> MagicMock:
      db = MagicMock()
      db.fetchval = AsyncMock(return_value=fetchval_return)
      return db


  @pytest.fixture(autouse=True)
  def clear_cache():
      """Ensure a clean TTL cache state for every test."""
      invalidate_dup_count_cache()
      yield
      invalidate_dup_count_cache()


  class TestCountPersonDuplicates:
      async def test_cache_miss_queries_db(self):
          db = _make_db(5)
          result = await count_person_duplicates(db)
          assert result == 5
          db.fetchval.assert_awaited_once()

      async def test_cache_hit_skips_db(self):
          db = _make_db(3)
          await count_person_duplicates(db)       # prime cache
          db2 = _make_db(99)
          result = await count_person_duplicates(db2)  # should hit cache
          assert result == 3
          db2.fetchval.assert_not_awaited()

      async def test_invalidate_forces_refresh(self):
          db = _make_db(2)
          await count_person_duplicates(db)       # prime cache with 2
          invalidate_dup_count_cache()
          db2 = _make_db(7)
          result = await count_person_duplicates(db2)  # cache cleared → re-query
          assert result == 7
          db2.fetchval.assert_awaited_once()

      async def test_returns_zero_count(self):
          db = _make_db(0)
          assert await count_person_duplicates(db) == 0


  class TestGetPersonDupCount:
      async def test_returns_count_on_success(self):
          db = _make_db(4)
          result = await get_person_dup_count(db=db)
          assert result == 4

      async def test_returns_zero_on_db_error(self):
          db = MagicMock()
          db.fetchval = AsyncMock(side_effect=Exception("pg_trgm not installed"))
          result = await get_person_dup_count(db=db)
          assert result == 0

      async def test_uses_cached_value(self):
          db = _make_db(6)
          await get_person_dup_count(db=db)        # prime cache
          db2 = _make_db(99)
          result = await get_person_dup_count(db=db2)
          assert result == 6
          db2.fetchval.assert_not_awaited()

      async def test_error_does_not_poison_cache(self):
          """A failed query should not cache 0; next call re-queries."""
          db_bad = MagicMock()
          db_bad.fetchval = AsyncMock(side_effect=Exception("oops"))
          await get_person_dup_count(db=db_bad)   # fails → returns 0, cache not updated
          db_good = _make_db(3)
          result = await get_person_dup_count(db=db_good)
          assert result == 3
          db_good.fetchval.assert_awaited_once()
  ```

- [ ] **Step 3: Run tests and verify they pass**

  ```bash
  python -m pytest tests/api/admin/test_people_dups.py -v
  ```
  Expected: all 8 tests **PASS**.

- [ ] **Step 4: Commit**

  ```bash
  git add src/api/admin/people_dups.py tests/api/admin/test_people_dups.py
  git commit -m "#53 feat: add people_dups detection module with TTL cache"
  ```

---

## Task 3: Add `person_dup_count` dep to `people.py` and new routes

**Files:**
- Modify: `src/api/admin/people.py`

This task has two parts: (A) inject `person_dup_count` into existing routes, and (B) add the three new routes.

### Part A: Inject `person_dup_count` into existing routes

- [ ] **Step 1: Write a failing test that checks the people list passes `person_dup_count` to template**

  Add to `tests/api/admin/test_people_duplicates.py` (create the file):

  ```python
  """Integration tests for people duplicate detection and dismiss routes."""
  import asyncio
  import json
  import os

  import asyncpg
  import pytest
  from fastapi.testclient import TestClient

  from src.api.main import app
  from src.core.db import apply_schema, generate_id

  pytestmark = pytest.mark.integration

  AUTH_HEADERS = {
      "X-ExeDev-UserID": "usr_test",
      "X-ExeDev-Email": "admin@test.com",
  }
  HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


  def _get_dsn() -> str:
      dsn = os.environ.get("DATABASE_URL")
      if not dsn:
          pytest.skip("DATABASE_URL not set")
      return dsn


  async def _aconnect(dsn: str) -> asyncpg.Connection:
      conn = await asyncpg.connect(dsn)
      await apply_schema(conn)
      return conn


  @pytest.fixture
  def client():
      with TestClient(app) as c:
          yield c


  @pytest.fixture
  def person_pair():
      """Insert two near-duplicate people (id_a < id_b), yield (id_a, id_b), teardown."""
      dsn = _get_dsn()
      id_a, id_b = generate_id(), generate_id()
      if id_a > id_b:
          id_a, id_b = id_b, id_a

      async def setup():
          conn = await _aconnect(dsn)
          try:
              for pid, name in [
                  (id_a, "Jonathan Smithfield"),
                  (id_b, "Jonathan Smithfeld"),   # deliberate near-match
              ]:
                  await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
                  await conn.execute(
                      "INSERT INTO person_names"
                      " (id, person_id, name, is_canonical)"
                      " VALUES ($1, $2, $3, TRUE)",
                      generate_id(), pid, name,
                  )
          finally:
              await conn.close()

      async def teardown():
          conn = await asyncpg.connect(dsn)
          try:
              await conn.execute(
                  "DELETE FROM duplicate_dismissals"
                  " WHERE entity_a_id=$1 OR entity_b_id=$1"
                  " OR entity_a_id=$2 OR entity_b_id=$2",
                  id_a, id_b,
              )
              for pid in [id_a, id_b]:
                  await conn.execute(
                      "DELETE FROM person_names WHERE person_id=$1", pid
                  )
                  await conn.execute("DELETE FROM people WHERE id=$1", pid)
          finally:
              await conn.close()

      asyncio.run(setup())
      yield id_a, id_b
      asyncio.run(teardown())


  # ── List screen ─────────────────────────────────────────────────────────────

  def test_people_list_shows_duplicate_banner(client, person_pair):
      response = client.get("/admin/people/", headers=AUTH_HEADERS)
      assert response.status_code == 200
      assert "possible duplicate" in response.text.lower()
  ```

  Run: `python -m pytest tests/api/admin/test_people_duplicates.py::test_people_list_shows_duplicate_banner -v`
  Expected: **FAIL** — banner not in response (template doesn't render it yet).

- [ ] **Step 2: Update imports in `people.py`**

  In `src/api/admin/people.py`, update the import at the top:

  Find:
  ```python
  from src.api.admin.org_dups import get_org_dup_count
  ```

  Replace with:
  ```python
  from src.api.admin.org_dups import get_org_dup_count
  from src.api.admin.people_dups import (
      CANDIDATE_WHERE,
      get_person_dup_count,
      invalidate_dup_count_cache as invalidate_person_dup_count_cache,
  )
  ```

  Also add `is_htmx` and `flash_trigger` to the deps import if not already present:
  ```python
  from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
  ```

- [ ] **Step 3: Add `person_dup_count` dep to the four routes that render templates**

  The routes that currently inject `org_dup_count` (and thus need `person_dup_count` added) are exactly:
  - `people_list` (`GET /`)
  - `person_new_form` (`GET /new/`)
  - `person_detail` (`GET /{person_id}/`)
  - `person_edit_form` (`GET /{person_id}/edit/`)

  For each: add `person_dup_count: int = Depends(get_person_dup_count)` to the function signature, and add `"person_dup_count": person_dup_count` to the template context dict alongside `org_dup_count`.

- [ ] **Step 4: Add dup notice to `src/templates/admin/people/list.html`**

  Insert between the `<div class="page-header">` block and `<div class="filter-card">`:

  ```html
  {% if person_dup_count %}
  <div class="alert alert--notice" style="margin-bottom:var(--space-4);display:flex;align-items:center;justify-content:space-between;gap:var(--space-3)">
    <span>{{ person_dup_count }} possible duplicate person{{ 's' if person_dup_count != 1 else '' }} — <a href="/admin/people/duplicates/">Review</a></span>
    <button class="flash__close" aria-label="Dismiss" onclick="this.parentElement.remove()">×</button>
  </div>
  {% endif %}
  ```

- [ ] **Step 5: Run the banner test**

  ```bash
  export $(cat /etc/power-map/.env | xargs) 2>/dev/null
  export $(cat .env | xargs) 2>/dev/null
  python -m pytest tests/api/admin/test_people_duplicates.py::test_people_list_shows_duplicate_banner -v
  ```
  Expected: **PASS**.

- [ ] **Step 6: Commit Part A**

  ```bash
  git add src/api/admin/people.py src/templates/admin/people/list.html \
          tests/api/admin/test_people_duplicates.py
  git commit -m "#53 feat: inject person_dup_count into people routes and list banner"
  ```

### Part B: Add `/duplicates/` and dismiss routes

- [ ] **Step 7: Add the remaining integration tests**

  Append to `tests/api/admin/test_people_duplicates.py`:

  ```python
  # ── Duplicates review screen ─────────────────────────────────────────────────

  def test_duplicates_list_returns_200(client, person_pair):
      response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
      assert response.status_code == 200
      assert "duplicate" in response.text.lower()


  def test_duplicates_list_shows_pair(client, person_pair):
      response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
      assert "Jonathan Smithfield" in response.text


  # ── Dismiss ──────────────────────────────────────────────────────────────────

  def test_dismiss_pair_removes_from_list(client, person_pair):
      id_a, id_b = person_pair
      response = client.post(
          f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
          headers=AUTH_HEADERS,
          follow_redirects=False,
      )
      assert response.status_code in (302, 303)
      response2 = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
      assert response2.status_code == 200
      assert "Jonathan Smithfield" not in response2.text \
          and "Jonathan Smithfeld" not in response2.text


  def test_dismiss_htmx_returns_200_with_region(client, person_pair):
      id_a, id_b = person_pair
      response = client.post(
          f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
          headers=HTMX_HEADERS,
          follow_redirects=False,
      )
      assert response.status_code == 200
      assert "candidate" in response.text or "No duplicate" in response.text


  def test_dismiss_htmx_sends_hx_trigger_flash(client, person_pair):
      id_a, id_b = person_pair
      response = client.post(
          f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
          headers=HTMX_HEADERS,
          follow_redirects=False,
      )
      assert response.status_code == 200
      assert "HX-Trigger" in response.headers
      payload = json.loads(response.headers["HX-Trigger"])
      assert payload["showFlash"]["level"] == "info"
      assert "hx-swap-oob" not in response.text
  ```

  Run all new tests to confirm they fail:
  ```bash
  python -m pytest tests/api/admin/test_people_duplicates.py -v -k "not banner"
  ```
  Expected: **FAIL** — 404 on `/admin/people/duplicates/` (route not yet added).

- [ ] **Step 8: Add `_fetch_duplicate_pairs` and routes to `people.py`**

  Append to `src/api/admin/people.py` (before the closing of the file, after the last existing route):

  ```python
  async def _fetch_duplicate_pairs(db) -> list:
      """Return near-duplicate person pairs; empty list if pg_trgm not installed."""
      try:
          return await db.fetch(
              f"""SELECT
                  a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
                  b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
                  similarity(dn_a.display_name, dn_b.display_name) AS score,
                  (SELECT count(*) FROM role_assignments
                   WHERE person_id = a.id AND archived_at IS NULL) AS a_roles,
                  (SELECT count(*) FROM role_assignments
                   WHERE person_id = b.id AND archived_at IS NULL) AS b_roles
              {CANDIDATE_WHERE}
              ORDER BY score DESC"""
          )
      except asyncpg.exceptions.UndefinedFunctionError:
          return []


  @router.get("/duplicates/")
  async def people_duplicates(
      request: Request,
      user: AdminUser | RedirectResponse = Depends(get_admin_user),
      db=Depends(get_db),
      org_dup_count: int = Depends(get_org_dup_count),
      person_dup_count: int = Depends(get_person_dup_count),
  ):
      """List near-duplicate person pairs for review."""
      redirect, user = check_auth(user)
      if redirect:
          return redirect
      pairs = await _fetch_duplicate_pairs(db)
      ctx = {
          "user": user,
          "active_section": "people_duplicates",
          "pairs": pairs,
          "org_dup_count": org_dup_count,
          "person_dup_count": person_dup_count,
      }
      return templates.TemplateResponse(
          request,
          "admin/people/_duplicates_region.html"
          if is_htmx(request)
          else "admin/people/duplicates.html",
          ctx,
      )


  @router.post("/{id_a}/dismiss-duplicate/{id_b}/")
  async def person_dismiss_duplicate(
      id_a: str,
      id_b: str,
      request: Request,
      user: AdminUser | RedirectResponse = Depends(get_admin_user),
      db=Depends(get_db),
  ):
      """Record that this pair is not a duplicate (suppress from future results)."""
      redirect, user = check_auth(user)
      if redirect:
          return redirect
      # Store with consistent ordering (a < b)
      a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
      await db.execute(
          "INSERT INTO duplicate_dismissals"
          " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
          " VALUES ($1, 'person', $2, $3, $4)"
          " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
          generate_id(), a, b, user.email,
      )
      invalidate_person_dup_count_cache()
      if is_htmx(request):
          pairs = await _fetch_duplicate_pairs(db)
          ctx = {
              "user": user,
              "active_section": "people_duplicates",
              "pairs": pairs,
          }
          return templates.TemplateResponse(
              request,
              "admin/people/_duplicates_region.html",
              ctx,
              headers=flash_trigger("info", "Pair marked as not a duplicate."),
          )
      return RedirectResponse("/admin/people/duplicates/", status_code=303)
  ```

  `CANDIDATE_WHERE` was already added to the `people_dups` import in Part A Step 2 — no import change needed here.

- [ ] **Step 9: Create `src/templates/admin/people/duplicates.html`**

  ```html
  {% extends "admin/base.html" %}
  {% block title %}Duplicate People{% endblock %}
  {% block breadcrumb %}
    <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
    <a href="/admin/people/">People</a><span class="breadcrumb__sep">›</span>
    <span>Duplicates</span>
  {% endblock %}
  {% block content %}
  <div class="page-header">
    <h1>Duplicate People</h1>
  </div>
    {% include "admin/people/_duplicates_region.html" %}
  {% endblock %}
  ```

- [ ] **Step 10: Create `src/templates/admin/people/_duplicates_region.html`**

  ```html
  <div id="people-duplicates-region" class="table-wrapper" aria-live="polite" aria-atomic="false">
  {% if pairs %}
  <table class="data-table">
    <caption>{{ pairs|length }} candidate pair{{ 's' if pairs|length != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Record A</th>
        <th scope="col">Record B</th>
        <th scope="col">Score</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody>
      {% for p in pairs %}
      <tr>
        <td>
          <a href="/admin/people/{{ p.a_id }}/">{{ p.a_name }}</a><br>
          <small style="color:var(--color-text-muted)">{{ p.a_created.strftime('%Y-%m-%d') }} · {{ p.a_roles }} assignment{{ 's' if p.a_roles != 1 else '' }}</small>
        </td>
        <td>
          <a href="/admin/people/{{ p.b_id }}/">{{ p.b_name }}</a><br>
          <small style="color:var(--color-text-muted)">{{ p.b_created.strftime('%Y-%m-%d') }} · {{ p.b_roles }} assignment{{ 's' if p.b_roles != 1 else '' }}</small>
        </td>
        <td>{{ "%.0f%%"|format(p.score * 100) }}</td>
        <td class="dup-actions">
          <form hx-post="/admin/people/{{ p.a_id }}/dismiss-duplicate/{{ p.b_id }}/"
                hx-target="#people-duplicates-region" hx-swap="outerHTML"
                style="display:inline">
            <button class="btn btn--ghost btn--sm" type="submit">Not a duplicate</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:var(--color-text-muted);text-align:center;padding:2rem">No duplicate candidates found.</p>
  {% endif %}
  </div>
  ```

- [ ] **Step 11: Run the full test suite for this task**

  ```bash
  export $(cat /etc/power-map/.env | xargs) 2>/dev/null
  export $(cat .env | xargs) 2>/dev/null
  python -m pytest tests/api/admin/test_people_duplicates.py -v
  ```
  Expected: all tests **PASS**.

- [ ] **Step 12: Commit Part B**

  ```bash
  git add src/api/admin/people.py \
          src/templates/admin/people/duplicates.html \
          src/templates/admin/people/_duplicates_region.html \
          tests/api/admin/test_people_duplicates.py
  git commit -m "#53 feat: add people duplicates review screen and dismiss route"
  ```

---

## Task 4: Add sidebar badge in `base.html`

**Files:**
- Modify: `src/templates/admin/base.html` (line 51)

- [ ] **Step 1: Write the failing test**

  Add to `tests/api/admin/test_people_duplicates.py`:

  ```python
  def test_people_list_sidebar_badge_visible(client, person_pair):
      """Sidebar shows Duplicates link with count when person_dup_count > 0."""
      response = client.get("/admin/people/", headers=AUTH_HEADERS)
      assert response.status_code == 200
      assert "Duplicates" in response.text
      # Badge count in the sidebar link text
      assert f"({" in response.text  # crude check: count badge present
  ```

  Run: `python -m pytest tests/api/admin/test_people_duplicates.py::test_people_list_sidebar_badge_visible -v`
  Expected: **FAIL** — "Duplicates" link under People not in sidebar yet.

- [ ] **Step 2: Update `base.html` sidebar**

  In `src/templates/admin/base.html`, line 51 currently reads:
  ```html
  <a class="admin-sidebar__link" href="/admin/people/" {% if active_section == 'people' %}aria-current="page"{% endif %}>People</a>
  ```

  Insert a new line immediately after line 51 (the People link):
  ```html
  <a class="admin-sidebar__sublink" href="/admin/people/duplicates/" {% if active_section == 'people_duplicates' %}aria-current="page"{% endif %}>Duplicates{% if person_dup_count %} ({{ person_dup_count }}){% endif %}</a>
  ```

- [ ] **Step 3: Run the sidebar test**

  ```bash
  python -m pytest tests/api/admin/test_people_duplicates.py::test_people_list_sidebar_badge_visible -v
  ```
  Expected: **PASS**.

- [ ] **Step 4: Run the full non-integration suite to catch any regressions**

  ```bash
  python -m pytest tests/ -k "not integration" -v --tb=short -q
  ```
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/templates/admin/base.html tests/api/admin/test_people_duplicates.py
  git commit -m "#53 feat: add People › Duplicates sidebar badge"
  ```

---

## Task 5: Smoke test end-to-end on dev server

- [ ] **Step 1: Start dev server from main checkout**

  ```bash
  export $(cat /etc/power-map/.env | xargs) 2>/dev/null
  uvicorn src.api.main:app --port 8001 --reload
  ```

- [ ] **Step 2: Verify pages load**

  Open `https://power-map.exe.xyz:8001/admin/people/` — confirm no errors in server log.
  Open `https://power-map.exe.xyz:8001/admin/people/duplicates/` — confirm page renders (may show "No duplicate candidates found" if no near-matches in the DB).

- [ ] **Step 3: Run full integration test suite**

  ```bash
  export $(cat /etc/power-map/.env | xargs) 2>/dev/null
  export $(cat .env | xargs) 2>/dev/null
  python -m pytest tests/ --tb=short -q
  ```
  Expected: all tests pass.

- [ ] **Step 4: Final commit / close issue**

  If all tests pass and the dev server is clean, use the `shipping-work-claude` skill or run:
  ```bash
  git push
  gh issue close 53 --comment "Implemented: v_person_display_names view, people_dups.py detection module, dismissable notice on people list, /admin/people/duplicates/ review screen, sidebar badge. Merge tracked in #55."
  ```
