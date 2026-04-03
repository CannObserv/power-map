# People Merge Duplicate Pairs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Keep A" / "Keep B" merge buttons to the people duplicates review screen, backed by a route that consolidates two person records into one.

**Architecture:** Single new route `POST /admin/people/{winner_id}/merge/{loser_id}/` in `src/api/admin/people.py`, mirroring the org merge pattern. Runs all reassignments inside one transaction; hard-deletes the loser. Template update adds the two buttons. Tests follow the org-duplicates test pattern in `tests/api/admin/test_people_duplicates.py`.

**Tech Stack:** FastAPI, asyncpg, Jinja2/HTMX, pytest (integration)

---

### Task 1: Add merge route to `people.py`

**Files:**
- Modify: `src/api/admin/people.py`

- [ ] **Step 1: Add the route skeleton (will fail type-check/import if run, but write it)**

Add this route to `src/api/admin/people.py` immediately before the `person_dismiss_duplicate` route. Insert the `escape` import from `markupsafe` at the top of the file alongside existing imports.

```python
# top of file — add to existing imports block
from markupsafe import escape
```

```python
@router.post("/{winner_id}/merge/{loser_id}/")
async def person_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )

    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id FROM people WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id FROM people WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Person not found")

        # person_names: demote loser's canonical name to alias, reassign all to winner
        await db.execute(
            "UPDATE person_names SET is_canonical=FALSE"
            " WHERE person_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        await db.execute(
            "UPDATE person_names SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # role_assignments: delete conflicts, then reassign the rest
        await db.execute(
            """DELETE FROM role_assignments
               WHERE person_id=$1 AND archived_at IS NULL
                 AND (role_id, COALESCE(start_date, '0001-01-01')) IN (
                     SELECT role_id, COALESCE(start_date, '0001-01-01')
                     FROM role_assignments
                     WHERE person_id=$2 AND archived_at IS NULL
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            "UPDATE role_assignments SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # Polymorphic entity tables
        for table in ("contact_methods", "links", "entity_addresses",
                      "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='person' AND entity_id=$2",
                winner_id, loser_id,
            )

        # identifiers (no entity_type column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )

        # duplicate_dismissals: reassign other dismissals, delete the merged pair
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='person'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
        for old_id, new_id in [(loser_id, winner_id)]:
            a, b = (new_id, old_id) if new_id < old_id else (old_id, new_id)
            await db.execute(
                "UPDATE duplicate_dismissals SET entity_a_id=$1"
                " WHERE entity_type='person' AND entity_a_id=$2",
                winner_id, loser_id,
            )
            await db.execute(
                "UPDATE duplicate_dismissals SET entity_b_id=$1"
                " WHERE entity_type='person' AND entity_b_id=$2",
                winner_id, loser_id,
            )

        await db.execute("DELETE FROM people WHERE id=$1", loser_id)

    invalidate_person_dup_count_cache()

    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review role assignments and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)
```

Note: the `duplicate_dismissals` reassignment block above has a subtle bug — the loop with `(old_id, new_id)` tuple unpacking is misleading and the SQL already uses `$1/$2` correctly without the loop. Simplify to just the two UPDATE statements directly:

```python
        # Reassign any other dismissals that reference the loser
        await db.execute(
            "UPDATE duplicate_dismissals SET entity_a_id=$1"
            " WHERE entity_type='person' AND entity_a_id=$2",
            winner_id, loser_id,
        )
        await db.execute(
            "UPDATE duplicate_dismissals SET entity_b_id=$1"
            " WHERE entity_type='person' AND entity_b_id=$2",
            winner_id, loser_id,
        )
```

Use this corrected version. The full route with the fix applied:

```python
@router.post("/{winner_id}/merge/{loser_id}/")
async def person_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    winner_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", winner_id
    )
    loser_name = await db.fetchval(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", loser_id
    )

    async with db.transaction():
        winner = await db.fetchrow(
            "SELECT id FROM people WHERE id=$1 FOR UPDATE", winner_id
        )
        loser = await db.fetchrow(
            "SELECT id FROM people WHERE id=$1 FOR UPDATE", loser_id
        )
        if not winner or not loser:
            raise HTTPException(status_code=404, detail="Person not found")

        # person_names: demote loser's canonical to alias, then reassign all to winner
        await db.execute(
            "UPDATE person_names SET is_canonical=FALSE"
            " WHERE person_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        await db.execute(
            "UPDATE person_names SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # role_assignments: delete conflicts (same role+start_date on both), then reassign
        await db.execute(
            """DELETE FROM role_assignments
               WHERE person_id=$1 AND archived_at IS NULL
                 AND (role_id, COALESCE(start_date, '0001-01-01')) IN (
                     SELECT role_id, COALESCE(start_date, '0001-01-01')
                     FROM role_assignments
                     WHERE person_id=$2 AND archived_at IS NULL
                 )""",
            loser_id, winner_id,
        )
        await db.execute(
            "UPDATE role_assignments SET person_id=$1 WHERE person_id=$2",
            winner_id, loser_id,
        )

        # Polymorphic entity tables
        for table in ("contact_methods", "links", "entity_addresses",
                      "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='person' AND entity_id=$2",
                winner_id, loser_id,
            )

        # identifiers (no entity_type column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )

        # duplicate_dismissals: delete the merged pair, reassign any others referencing loser
        await db.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_type='person'"
            "   AND ((entity_a_id=$1 AND entity_b_id=$2)"
            "    OR  (entity_a_id=$2 AND entity_b_id=$1))",
            winner_id, loser_id,
        )
        await db.execute(
            "UPDATE duplicate_dismissals SET entity_a_id=$1"
            " WHERE entity_type='person' AND entity_a_id=$2",
            winner_id, loser_id,
        )
        await db.execute(
            "UPDATE duplicate_dismissals SET entity_b_id=$1"
            " WHERE entity_type='person' AND entity_b_id=$2",
            winner_id, loser_id,
        )

        await db.execute("DELETE FROM people WHERE id=$1", loser_id)

    invalidate_person_dup_count_cache()

    if is_htmx(request):
        pairs = await _fetch_duplicate_pairs(db)
        body = (
            f'Merged <strong>{escape(loser_name)}</strong> into '
            f'<a href="/admin/people/{winner_id}/"><strong>{escape(winner_name)}</strong></a>. '
            f'Review role assignments and contact info for duplicates.'
        )
        ctx = {
            "user": user,
            "active_section": "people_duplicates",
            "pairs": pairs,
        }
        return templates.TemplateResponse(
            request,
            "admin/people/_duplicates_region.html",
            ctx,
            headers=flash_trigger("success", body),
        )
    return RedirectResponse("/admin/people/duplicates/", status_code=303)
```

- [ ] **Step 2: Verify import is already present or add it**

Check the imports block in `src/api/admin/people.py`. `markupsafe` is a Jinja2 transitive dep — add `from markupsafe import escape` if not already there.

---

### Task 2: Write integration tests for merge

**Files:**
- Modify: `tests/api/admin/test_people_duplicates.py`

- [ ] **Step 1: Add a `person_pair_with_roles` fixture**

This fixture adds two people who each hold the same role (to test conflict deletion) plus a unique role each (to test reassignment). Add it after the existing `person_pair` fixture:

```python
@pytest.fixture
def person_pair_with_roles():
    """
    Two near-duplicate people, each with:
      - one shared role+start_date (conflict → loser's deleted on merge)
      - one unique role (reassigned to winner on merge)
    Yields (id_winner, id_loser, shared_role_id, unique_role_id_loser).
    """
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a
    id_org = generate_id()
    shared_role_id = generate_id()
    unique_role_id = generate_id()
    ra_a_shared = generate_id()
    ra_b_shared = generate_id()
    ra_b_unique = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id) VALUES ($1)", id_org
            )
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(), id_org,
            )
            for pid, name in [(id_a, "Jonathan Smithfield"),
                              (id_b, "Jonathan Smithfield Jr")]:
                await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
                await conn.execute(
                    "INSERT INTO person_names (id, person_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), pid, name,
                )
            for rid, title in [(shared_role_id, "Director"),
                               (unique_role_id, "Advisor")]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title)"
                    " VALUES ($1, $2, $3)",
                    rid, id_org, title,
                )
            # Both people hold shared_role starting 2024-01-01
            for ra_id, pid in [(ra_a_shared, id_a), (ra_b_shared, id_b)]:
                await conn.execute(
                    "INSERT INTO role_assignments"
                    " (id, person_id, role_id, is_current, start_date)"
                    " VALUES ($1, $2, $3, FALSE, '2024-01-01')",
                    ra_id, pid, shared_role_id,
                )
            # Only loser holds unique_role
            await conn.execute(
                "INSERT INTO role_assignments"
                " (id, person_id, role_id, is_current)"
                " VALUES ($1, $2, $3, TRUE)",
                ra_b_unique, id_b, unique_role_id,
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
            await conn.execute(
                "DELETE FROM role_assignments WHERE person_id=$1 OR person_id=$2",
                id_a, id_b,
            )
            await conn.execute(
                "DELETE FROM roles WHERE id=$1 OR id=$2",
                shared_role_id, unique_role_id,
            )
            for pid in [id_a, id_b]:
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id=$1", id_org
            )
            await conn.execute("DELETE FROM organizations WHERE id=$1", id_org)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield id_a, id_b, shared_role_id, unique_role_id
    asyncio.run(teardown())
```

- [ ] **Step 2: Write the merge test cases**

Add these tests after the existing dismiss tests:

```python
# ── Merge ────────────────────────────────────────────────────────────────────

def test_merge_hard_deletes_loser(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchrow("SELECT id FROM people WHERE id=$1", id_b)
        finally:
            await conn.close()

    assert asyncio.run(check()) is None


def test_merge_reassigns_loser_names_as_aliases(client, person_pair):
    id_a, id_b = person_pair

    async def get_names_before():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetch(
                "SELECT name, is_canonical FROM person_names WHERE person_id=$1", id_b
            )
        finally:
            await conn.close()

    loser_names = asyncio.run(get_names_before())
    loser_canonical = next(r["name"] for r in loser_names if r["is_canonical"])

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetch(
                "SELECT name, is_canonical FROM person_names WHERE person_id=$1", id_a
            )
        finally:
            await conn.close()

    winner_names = asyncio.run(check())
    winner_name_strs = {r["name"] for r in winner_names}
    # Loser's canonical name now exists on winner as a non-canonical alias
    assert loser_canonical in winner_name_strs
    canonical_rows = [r for r in winner_names if r["is_canonical"]]
    assert len(canonical_rows) == 1  # exactly one canonical remains


def test_merge_deletes_conflicting_role_assignment(client, person_pair_with_roles):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles
    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetch(
                "SELECT id FROM role_assignments"
                " WHERE person_id=$1 AND role_id=$2",
                id_winner, shared_role_id,
            )
        finally:
            await conn.close()

    rows = asyncio.run(check())
    assert len(rows) == 1  # only winner's original assignment remains


def test_merge_reassigns_unique_role_assignment(client, person_pair_with_roles):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles
    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def check():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchrow(
                "SELECT id FROM role_assignments"
                " WHERE person_id=$1 AND role_id=$2",
                id_winner, unique_role_id,
            )
        finally:
            await conn.close()

    assert asyncio.run(check()) is not None  # loser's unique role now on winner


def test_merge_returns_404_for_unknown_person(client, person_pair):
    id_a, _ = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/nonexistent-id/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_merge_preserves_winner_existing_roles(client, person_pair_with_roles):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles

    async def get_winner_role_count_before():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM role_assignments WHERE person_id=$1",
                id_winner,
            )
        finally:
            await conn.close()

    count_before = asyncio.run(get_winner_role_count_before())

    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async def get_winner_role_count_after():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM role_assignments WHERE person_id=$1",
                id_winner,
            )
        finally:
            await conn.close()

    count_after = asyncio.run(get_winner_role_count_after())
    # Winner had 1 role; loser had 1 shared (deleted) + 1 unique (reassigned) = winner ends up with 2
    assert count_after == count_before + 1  # shared conflict deleted; unique role added


def test_merge_htmx_returns_200_with_region(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


def test_merge_htmx_sends_hx_trigger_flash(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Jonathan Smithfield" in payload["showFlash"]["body"]
    assert f"/admin/people/{id_a}/" in payload["showFlash"]["body"]
    assert "hx-swap-oob" not in response.text
```

- [ ] **Step 3: Run the new tests to confirm they fail**

```bash
cd /home/exedev/power-map/.worktrees/55-people-merge
export $(cat /etc/power-map/.env | xargs) && export $(cat .env | xargs)
uv run pytest tests/api/admin/test_people_duplicates.py -k "merge" -v --no-cov
```

Expected: all merge tests fail with 404 or 405 (route doesn't exist yet).

- [ ] **Step 4: Implement the route (Task 1 code)**

Apply Task 1's route code to `src/api/admin/people.py`.

- [ ] **Step 5: Run tests again to confirm they pass**

```bash
uv run pytest tests/api/admin/test_people_duplicates.py -v --no-cov
```

Expected: all tests pass including the new merge tests.

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/people.py tests/api/admin/test_people_duplicates.py
git commit -m "#55 feat: add people merge route with role conflict resolution"
```

---

### Task 3: Add "Keep A" / "Keep B" buttons to the template

**Files:**
- Modify: `src/templates/admin/people/_duplicates_region.html`

- [ ] **Step 1: Update `dup-actions` cell**

Replace the existing actions `<td>` in `_duplicates_region.html`:

```html
{# existing #}
      <td class="dup-actions">
        <form hx-post="/admin/people/{{ p.a_id }}/dismiss-duplicate/{{ p.b_id }}/"
              hx-target="#people-duplicates-region" hx-swap="outerHTML"
              style="display:inline">
          <button class="btn btn--ghost btn--sm" type="submit">Not a duplicate</button>
        </form>
      </td>
```

Replace with:

```html
      <td class="dup-actions">
        <form hx-post="/admin/people/{{ p.a_id }}/merge/{{ p.b_id }}/"
              hx-target="#people-duplicates-region" hx-swap="outerHTML"
              style="display:inline">
          <button class="btn btn--primary btn--sm" type="submit">Keep A</button>
        </form>
        <form hx-post="/admin/people/{{ p.b_id }}/merge/{{ p.a_id }}/"
              hx-target="#people-duplicates-region" hx-swap="outerHTML"
              style="display:inline">
          <button class="btn btn--primary btn--sm" type="submit">Keep B</button>
        </form>
        <form hx-post="/admin/people/{{ p.a_id }}/dismiss-duplicate/{{ p.b_id }}/"
              hx-target="#people-duplicates-region" hx-swap="outerHTML"
              style="display:inline">
          <button class="btn btn--ghost btn--sm" type="submit">Not a duplicate</button>
        </form>
      </td>
```

- [ ] **Step 2: Write a template test**

Add to `tests/api/admin/test_people_templates.py` (or `test_people_duplicates.py` if it fits better):

```python
def test_duplicates_region_has_keep_a_keep_b_buttons(client, person_pair):
    id_a, id_b = person_pair
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Keep A" in response.text
    assert "Keep B" in response.text
    assert f"/admin/people/{id_a}/merge/{id_b}/" in response.text
    assert f"/admin/people/{id_b}/merge/{id_a}/" in response.text
```

- [ ] **Step 3: Run to confirm test fails**

```bash
uv run pytest tests/api/admin/test_people_duplicates.py -k "keep_a_keep_b" -v --no-cov
```

Expected: FAIL — "Keep A" not found.

- [ ] **Step 4: Apply the template change**

- [ ] **Step 5: Run tests to confirm pass**

```bash
uv run pytest tests/api/admin/test_people_duplicates.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/people/_duplicates_region.html \
        tests/api/admin/test_people_duplicates.py
git commit -m "#55 feat: add Keep A / Keep B merge buttons to people duplicates UI"
```

---

### Task 4: Full test suite green check

- [ ] **Step 1: Run full suite**

```bash
uv run pytest --no-cov -q
```

Expected: all previously-passing tests still pass; merge/template tests pass.

- [ ] **Step 2: If any failures, fix before proceeding**
