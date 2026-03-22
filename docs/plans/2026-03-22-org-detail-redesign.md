# Org Detail Screen Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the org detail admin screen with inline HTMX editing for all fields; consolidate `urls`/`social_links`/`url_types`/`platforms` into unified `links`/`link_types` tables.

**Architecture:** Two independent milestones. Milestone A (Tasks 1–4): data model + ingestion pipeline — safe to merge and deploy on its own. Milestone B (Tasks 5–15): admin UI redesign and inline editing — depends on Milestone A. Row-level HTMX swap (`hx-target="closest tr"`, `hx-swap="outerHTML"`) is the editing pattern throughout.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Jinja2, HTMX, PostgreSQL 15+, uv/pytest

**Design doc:** `docs/plans/2026-03-22-org-detail-redesign-design.md`

---

## File Map

### Milestone A — Data model

| Action | Path |
|---|---|
| Modify | `src/core/schema.sql` |
| Modify | `src/core/ingestion/pipeline.py` |
| Modify | `src/core/ingestion/sources/csv_org.py` |
| Modify | `src/core/ingestion/sources/csv_person.py` |
| Modify | `src/core/ingestion/sources/csv_role.py` |
| Modify | `src/api/admin/orgs.py` (detail query + merge handler) |
| Create | `tests/core/test_schema_links.py` |
| Modify | `tests/api/admin/test_orgs.py` (remove /edit/ tests after Task 15) |

### Milestone B — Admin UI

| Action | Path |
|---|---|
| Modify | `src/templates/admin/orgs/detail.html` |
| Create | `src/templates/admin/orgs/partials/_search_results.html` |
| Create | `src/templates/admin/orgs/partials/_core_fields_read.html` |
| Create | `src/templates/admin/orgs/partials/_core_fields_form.html` |
| Create | `src/templates/admin/orgs/partials/_parent_read.html` |
| Create | `src/templates/admin/orgs/partials/_parent_form.html` |
| Create | `src/templates/admin/orgs/partials/_name_row.html` |
| Create | `src/templates/admin/orgs/partials/_name_form_row.html` |
| Create | `src/templates/admin/orgs/partials/_address_row.html` |
| Create | `src/templates/admin/orgs/partials/_address_form_row.html` |
| Create | `src/templates/admin/orgs/partials/_contact_row.html` |
| Create | `src/templates/admin/orgs/partials/_contact_form_row.html` |
| Create | `src/templates/admin/orgs/partials/_link_row.html` |
| Create | `src/templates/admin/orgs/partials/_link_form_row.html` |
| Create | `src/templates/admin/orgs/partials/_identifier_row.html` |
| Create | `src/templates/admin/orgs/partials/_identifier_form_row.html` |
| Create | `src/templates/admin/orgs/partials/_child_row.html` |
| Create | `src/templates/admin/orgs/partials/_child_form_row.html` |
| Modify | `src/api/admin/orgs.py` (search, inline/core, inline/parent, children) |
| Create | `src/api/admin/orgs_names.py` |
| Create | `src/api/admin/orgs_addresses.py` |
| Create | `src/api/admin/orgs_contacts.py` |
| Create | `src/api/admin/orgs_links.py` |
| Create | `src/api/admin/orgs_identifiers.py` |
| Modify | `src/api/admin/router.py` |
| Create | `tests/api/admin/test_orgs_inline.py` |
| Create | `tests/api/admin/test_orgs_names.py` |
| Create | `tests/api/admin/test_orgs_addresses.py` |
| Create | `tests/api/admin/test_orgs_contacts.py` |
| Create | `tests/api/admin/test_orgs_links.py` |
| Create | `tests/api/admin/test_orgs_identifiers.py` |
| Create | `tests/api/admin/test_orgs_children.py` |

---

## Task 1: Schema — `link_types` and `links` tables

**Files:**
- Modify: `src/core/schema.sql`
- Create: `tests/core/test_schema_links.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_schema_links.py
"""Integration tests for link_types and links schema."""

import asyncio
import os
import pytest
import asyncpg
from src.core.db import apply_schema

pytestmark = pytest.mark.integration


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _conn() -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn())
    await apply_schema(conn)
    return conn


def test_link_types_table_exists():
    async def run():
        conn = await _conn()
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'link_types')"
            )
            assert exists, "link_types table must exist"
        finally:
            await conn.close()
    asyncio.run(run())


def test_link_types_social_flags_correct():
    """Twitter/Bluesky/LinkedIn must be social=TRUE; website/profile must be FALSE."""
    async def run():
        conn = await _conn()
        try:
            social = await conn.fetchval(
                "SELECT is_social FROM link_types WHERE slug = 'twitter'"
            )
            assert social is True, "twitter must be social"
            generic = await conn.fetchval(
                "SELECT is_social FROM link_types WHERE slug = 'website'"
            )
            assert generic is False, "website must not be social"
        finally:
            await conn.close()
    asyncio.run(run())


def test_links_table_exists():
    async def run():
        conn = await _conn()
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'links')"
            )
            assert exists, "links table must exist"
        finally:
            await conn.close()
    asyncio.run(run())


def test_old_tables_absent():
    """urls, social_links, url_types, platforms must not exist after migration."""
    async def run():
        conn = await _conn()
        try:
            for table in ("urls", "social_links", "url_types", "platforms"):
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                    " WHERE table_name = $1)",
                    table,
                )
                assert not exists, f"{table} must be dropped after migration"
        finally:
            await conn.close()
    asyncio.run(run())


def test_apply_schema_idempotent():
    """Running apply_schema twice must not raise."""
    async def run():
        conn = await _conn()
        try:
            await apply_schema(conn)  # second run
        finally:
            await conn.close()
    asyncio.run(run())
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/core/test_schema_links.py -v -m integration
```
Expected: FAIL — `link_types` table does not exist.

- [ ] **Step 3: Edit `src/core/schema.sql` — Lookup / Reference Tables section**

Replace the `platforms` and `url_types` CREATE TABLE blocks with `link_types`:

```sql
-- Remove these two blocks entirely:
-- CREATE TABLE IF NOT EXISTS platforms (...)
-- CREATE TABLE IF NOT EXISTS url_types (...)

-- Add this block in their place:
CREATE TABLE IF NOT EXISTS link_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    is_social    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- [ ] **Step 4: Edit `src/core/schema.sql` — replace `urls` and `social_links` with `links`**

Remove the `urls` and `social_links` CREATE TABLE blocks and their indexes. Add:

```sql
CREATE TABLE IF NOT EXISTS links (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL
                              CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment')),
    entity_id     TEXT        NOT NULL,
    url           TEXT        NOT NULL,
    link_type_id  TEXT        NOT NULL REFERENCES link_types(id),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    is_canonical  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_links_entity
    ON links(entity_type, entity_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_link_canonical
    ON links(entity_type, entity_id)
    WHERE is_canonical = TRUE;
```

- [ ] **Step 5: Edit `src/core/schema.sql` — add migration DO block**

Add this block after the `links` table definition, before the Seed Data section:

```sql
-- =============================================================================
-- Migration: urls/social_links/url_types/platforms → link_types/links
-- Idempotent: checks table existence before operating. Safe to re-run.
-- =============================================================================
DO $$
BEGIN
    -- Migrate url_types → link_types (is_social = FALSE)
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'url_types'
    ) THEN
        INSERT INTO link_types (id, slug, display_name, is_social)
        SELECT id, slug, display_name, FALSE FROM url_types
        ON CONFLICT (slug) DO NOTHING;
    END IF;

    -- Migrate platforms → link_types (is_social = TRUE)
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'platforms'
    ) THEN
        INSERT INTO link_types (id, slug, display_name, is_social)
        SELECT id, slug, display_name, TRUE FROM platforms
        ON CONFLICT (slug) DO NOTHING;
    END IF;

    -- Migrate urls → links
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'urls'
    ) THEN
        INSERT INTO links (id, entity_type, entity_id, url, link_type_id,
                           is_active, is_canonical, created_at)
        SELECT u.id, u.entity_type, u.entity_id, u.url,
               lt.id, TRUE, u.is_canonical, u.created_at
        FROM urls u
        JOIN url_types ut ON ut.id = u.url_type_id
        JOIN link_types lt ON lt.slug = ut.slug
        ON CONFLICT (id) DO NOTHING;

        DROP TABLE urls;
    END IF;

    -- Migrate social_links → links
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'social_links'
    ) THEN
        INSERT INTO links (id, entity_type, entity_id, url, link_type_id,
                           is_active, is_canonical, created_at)
        SELECT sl.id, sl.entity_type, sl.entity_id, sl.url,
               lt.id, TRUE, FALSE, sl.created_at
        FROM social_links sl
        JOIN platforms p ON p.id = sl.platform_id
        JOIN link_types lt ON lt.slug = p.slug
        ON CONFLICT (id) DO NOTHING;

        DROP TABLE social_links;
    END IF;

    -- Drop old lookup tables (no longer referenced)
    DROP TABLE IF EXISTS url_types;
    DROP TABLE IF EXISTS platforms;
END $$;
```

- [ ] **Step 6: Edit `src/core/schema.sql` — update seed data section**

Replace the `INSERT INTO platforms` and `INSERT INTO url_types` blocks with a single `INSERT INTO link_types`:

```sql
INSERT INTO link_types (id, slug, display_name, is_social) VALUES
    ('01KKZ3WGJRPV2TDZV672NWFE8G', 'twitter',      'Twitter / X',                      TRUE),
    ('01KKZ3WGJRPV2TDZV672NWFE8H', 'bluesky',      'Bluesky',                          TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVA', 'linkedin',     'LinkedIn',                         TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVB', 'mastodon',     'Mastodon',                         TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVC', 'instagram',    'Instagram',                        TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVD', 'facebook',     'Facebook',                         TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVE', 'youtube',      'YouTube',                          TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVF', 'flickr',       'Flickr',                           TRUE),
    ('01KKZ3WGJSZF0F96SMYC000AVG', 'website',      'Official Website',                 FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVH', 'profile',      'Profile',                          FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVJ', 'wa_pdc',       'WA Public Disclosure Commission',  FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVK', 'sec_form_d',   'SEC Form D',                       FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVM', 'wikipedia',    'Wikipedia',                        FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVN', 'other',        'Other',                            FALSE),
    ('01KM0YSNEMMPY35FSS3CX49SFJ', 'google_drive', 'Google Drive',                     FALSE)
ON CONFLICT (slug) DO NOTHING;
```

**Important:** The IDs above are the same as the existing `platforms` and `url_types` seed rows — this preserves FK integrity for any rows referencing them during migration. Verify by cross-checking against the existing schema.sql seed data before committing.

- [ ] **Step 7: Run tests — confirm all pass**

```bash
uv run pytest tests/core/test_schema_links.py -v -m integration
```
Expected: all 5 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema_links.py
git commit -m "$(cat <<'EOF'
#27 feat: add link_types and links tables, migrate from urls/social_links

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Update ingestion pipeline

**Files:**
- Modify: `src/core/ingestion/pipeline.py`
- Modify: `src/core/ingestion/sources/csv_org.py`
- Modify: `src/core/ingestion/sources/csv_person.py`
- Modify: `src/core/ingestion/sources/csv_role.py`

- [ ] **Step 1: Run existing ingestion tests to establish baseline**

```bash
uv run pytest tests/core/ -v -m integration
```
Note which tests pass now — they should all pass (migration DO block won't fire until the test DB is upgraded, but the CREATE TABLE IF NOT EXISTS for the new tables will succeed alongside the old ones on first run; on a fresh test DB there are no old tables so migration skips).

- [ ] **Step 2: Update `ReferenceData` and `_load_reference_data` in `pipeline.py`**

Replace `url_type_ids` + `platform_ids` with `link_type_ids`:

```python
# In ReferenceData dataclass — replace:
#   url_type_ids: dict[str, str] = field(default_factory=dict)
#   platform_ids: dict[str, str] = field(default_factory=dict)
# With:
link_type_ids: dict[str, str] = field(default_factory=dict)  # slug → id

# In _load_reference_data — replace the two fetch loops with:
for row in await conn.fetch("SELECT id, slug FROM link_types"):
    ref.link_type_ids[row["slug"]] = row["id"]
```

- [ ] **Step 3: Update INSERT statements in `pipeline.py`**

There are three INSERT sites (one per entity type: org, person, role). For each `urls` block:

```python
# Replace:
for u in t["urls"]:
    url_type_id = ref.url_type_ids.get(u["url_type_slug"])
    if url_type_id:
        await conn.execute(
            "INSERT INTO urls"
            " (id, entity_type, entity_id, url, url_type_id, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            generate_id(), entity_type, entity_id, u["url"], url_type_id, u["is_canonical"],
        )
for sl in t["social_links"]:
    platform_id = ref.platform_ids.get(sl["platform_slug"])
    if platform_id:
        await conn.execute(
            "INSERT INTO social_links"
            " (id, entity_type, entity_id, platform_id, url)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(), entity_type, entity_id, platform_id, sl["url"],
        )

# With:
for lnk in t["links"]:
    link_type_id = ref.link_type_ids.get(lnk["link_type_slug"])
    if link_type_id:
        await conn.execute(
            "INSERT INTO links"
            " (id, entity_type, entity_id, url, link_type_id, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            generate_id(), entity_type, entity_id,
            lnk["url"], link_type_id, lnk.get("is_canonical", False),
        )
```

Apply this change at all three INSERT sites (search for `"INSERT INTO urls"` in pipeline.py).

- [ ] **Step 4: Update CSV source files**

The same change applies to `csv_org.py`, `csv_person.py`, `csv_role.py`. In each file, replace the separate `urls` and `social_links` lists with a single `links` list:

```python
# Replace:
urls: list[dict] = []
# ... (url building logic, appending {"url": ..., "url_type_slug": ..., "is_canonical": ...})
social_links: list[dict] = []
# ... (social building logic, appending {"platform_slug": ..., "url": ...})

# With:
links: list[dict] = []
# URLs: keep existing logic but append to links with link_type_slug
links.append({"url": r.value, "link_type_slug": url_type_slug, "is_canonical": is_canonical})
# Social: keep existing logic but append to links with link_type_slug
links.append({"link_type_slug": platform_slug, "url": r.value, "is_canonical": False})
```

In the returned dict, replace `"urls": urls, "social_links": social_links` with `"links": links`.

- [ ] **Step 5: Run ingestion tests**

```bash
uv run pytest tests/core/ -v -m integration
```
Expected: all pass. If any ingestion integration tests reference `url_type_ids` or `platform_ids`, update them to `link_type_ids`.

- [ ] **Step 6: Commit**

```bash
git add src/core/ingestion/
git commit -m "$(cat <<'EOF'
#27 refactor: update ingestion pipeline to use links/link_types

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update org routes to use `links` table

**Files:**
- Modify: `src/api/admin/orgs.py`

The `org_detail` handler queries `urls` and `social` separately; `org_merge` reassigns both. Both must be updated.

- [ ] **Step 1: Update `org_detail` query in `orgs.py`**

Replace the two separate fetches for `urls` and `social` with one:

```python
links = await db.fetch(
    """SELECT l.*, lt.display_name AS link_type_name, lt.is_social
       FROM links l
       JOIN link_types lt ON lt.id = l.link_type_id
       WHERE l.entity_type = 'organization' AND l.entity_id = $1
       ORDER BY lt.is_social, lt.display_name""",
    org_id,
)
```

Pass `links=links` to the template context. Remove the old `urls` and `social` context keys.

- [ ] **Step 2: Update `org_merge` in `orgs.py`**

In the merge transaction, replace the `urls` and `social_links` update/reassign steps with a single `links` update:

```python
# Replace:
# The url is_canonical demote block (loser's canonical url)
# The entity table loop that includes "urls", "social_links"

# With — add links to the polymorphic table loop:
for table in ("entity_addresses", "contact_methods", "links",
              "import_provenance", "field_confidence"):
    await db.execute(
        f"UPDATE {table} SET entity_id=$1"
        f" WHERE entity_type='organization' AND entity_id=$2",
        winner_id, loser_id,
    )
```

Also remove the canonical-url demote block (it was specific to the old `urls` table's `uq_url_canonical` index; `links` has `uq_link_canonical` which covers the same case via the same demote pattern — add it back for `links`):

```python
# Demote loser's canonical link if winner already has one
await db.execute(
    "UPDATE links SET is_canonical=FALSE"
    " WHERE entity_type='organization' AND entity_id=$1 AND is_canonical=TRUE"
    " AND EXISTS ("
    "   SELECT 1 FROM links"
    "   WHERE entity_type='organization' AND entity_id=$2 AND is_canonical=TRUE"
    " )",
    loser_id, winner_id,
)
```

- [ ] **Step 3: Run existing org tests**

```bash
uv run pytest tests/api/admin/test_orgs.py tests/api/admin/test_orgs_duplicates.py -v -m integration
```
Expected: all pass (detail page renders, merge works).

- [ ] **Step 4: Commit**

```bash
git add src/api/admin/orgs.py
git commit -m "$(cat <<'EOF'
#27 refactor: update org detail and merge to use links table

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

> ✅ **Milestone A complete** — data model migrated, ingestion pipeline updated, admin routes updated. Safe to merge and deploy independently before continuing to Milestone B.

---

## Task 4: Org detail layout redesign

**Files:**
- Modify: `src/templates/admin/orgs/detail.html`

This task is template-only — no new routes. It restructures the existing page per the design spec.

- [ ] **Step 1: Verify detail page still renders**

```bash
# Start dev server
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
# Manually verify https://power-map.exe.xyz/admin/orgs/01KM1CTH2ZJ9X098EJV3S6GNWG/
```

- [ ] **Step 2: Rewrite `detail.html` — page header and core fields card**

Replace the current top of the page (page-header + entity-card) with:

```html
{% extends "admin/base.html" %}
{% set _display = (names[0].name if names else (acronyms[0].acronym if acronyms else None)) %}
{% block title %}{{ (_display or org.id) }} — Organization{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a><span class="breadcrumb__sep">›</span>
  <span>{{ _display or org.id }}</span>
{% endblock %}
{% block content %}
<div class="page-header">
  <h1>
    {{ _display or '(unnamed)' }}
    {% if org.archived_at %}<span class="badge badge--archived">Archived</span>
    {% elif not org.active %}<span class="badge badge--inactive">Inactive</span>
    {% else %}<span class="badge badge--active">Active</span>{% endif %}
  </h1>
</div>

{# Core fields card — inline editing wired in Task 6 #}
<div class="entity-card" id="core-fields">
  {% include "admin/orgs/partials/_core_fields_read.html" %}
</div>
```

- [ ] **Step 3: Rewrite `detail.html` — Hierarchy section**

Replace the standalone "Child Organizations" section with a unified Hierarchy section:

```html
<section class="entity-section" id="section-hierarchy">
  <h2>Hierarchy</h2>
  {% if org.parent_id %}
  <div class="entity-card" style="margin-bottom:var(--space-4)">
    {% include "admin/orgs/partials/_parent_read.html" %}
  </div>
  {% else %}
  <div class="entity-card" style="margin-bottom:var(--space-4)">
    <p style="color:var(--color-text-muted)">No parent organization.
      <button class="btn btn--sm btn--secondary" ...>Set parent</button>
    </p>
  </div>
  {% endif %}
  <h3 style="margin-bottom:var(--space-2)">Child Organizations</h3>
  <div class="table-wrapper">
    <table class="data-table" id="children-table">
      <thead><tr><th>Name</th><th>Status</th><th></th></tr></thead>
      <tbody>
        {% for child in children %}{% include "admin/orgs/partials/_child_row.html" %}{% endfor %}
        {% if not children %}
        <tr><td colspan="3" style="text-align:center;color:var(--color-text-muted)">No child organizations</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
  <button class="btn btn--sm btn--secondary" style="margin-top:var(--space-2)"
          hx-get="/admin/orgs/{{ org.id }}/children/new-row/"
          hx-target="#children-table tbody"
          hx-swap="afterbegin">+ Add child</button>
</section>
```

- [ ] **Step 4: Rewrite `detail.html` — Names, Addresses, Contact Methods, Links, Identifiers sections**

Each section follows this pattern (example: Names):

```html
<section class="entity-section" id="section-names">
  <h2>Names</h2>
  <div class="table-wrapper">
    <table class="data-table" id="names-table">
      <thead><tr><th>Name</th><th>Type</th><th>Canonical</th><th></th></tr></thead>
      <tbody>
        {% for n in names %}{% include "admin/orgs/partials/_name_row.html" %}{% endfor %}
        {% for a in acronyms %}...{% endfor %}
        {% if not names and not acronyms %}
        <tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No names</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
  <button class="btn btn--sm btn--secondary" style="margin-top:var(--space-2)"
          hx-get="/admin/orgs/{{ org.id }}/names/new-row/"
          hx-target="#names-table tbody"
          hx-swap="afterbegin">+ Add name</button>
</section>
```

Apply this pattern to Addresses, Contact Methods, Links, and Identifiers. For Links, the section title is "Links" (not "URLs" or "Social Links").

- [ ] **Step 5: Rewrite `detail.html` — Roles section with client-side filter**

```html
<section class="entity-section">
  <h2>Roles</h2>
  <input type="search" id="roles-filter" placeholder="Filter roles…"
         style="margin-bottom:var(--space-2);width:100%;max-width:24rem"
         aria-label="Filter roles by title">
  <div class="table-wrapper">
    <table class="data-table" id="roles-table">
      <thead><tr><th scope="col">Title</th></tr></thead>
      <tbody>
        {% for role in roles %}
        <tr data-title="{{ role.title | lower }}">
          <td><a href="/admin/roles/{{ role.id }}/">{{ role.title or '(untitled)' }}</a></td>
        </tr>
        {% else %}
        <tr><td style="text-align:center;color:var(--color-text-muted)">No roles</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
<script>
  document.getElementById('roles-filter').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#roles-table tbody tr[data-title]').forEach(row => {
      row.hidden = q && !row.dataset.title.includes(q);
    });
  });
</script>
```

- [ ] **Step 6: Rewrite `detail.html` — Metadata and Danger Zone**

```html
<p class="text-muted" style="font-size:var(--font-sm);margin-top:var(--space-6)">
  <strong>Metadata</strong> &nbsp;·&nbsp;
  ID: <code>{{ org.id }}</code> &nbsp;·&nbsp;
  Created: {{ org.created_at.strftime('%Y-%m-%d') }} &nbsp;·&nbsp;
  Updated: {{ org.updated_at.strftime('%Y-%m-%d') }}
  {% if org.archived_at %} &nbsp;·&nbsp; Archived: {{ org.archived_at.strftime('%Y-%m-%d') }}{% endif %}
</p>

{# Danger Zone — unchanged from current detail.html #}
```

- [ ] **Step 7: Manually verify layout in browser**

Navigate to an org with rich data (e.g. WSLCB). Confirm: status badge in header, no ID at top, hierarchy section present, roles filter works, metadata line at bottom.

- [ ] **Step 8: Commit**

```bash
git add src/templates/admin/orgs/detail.html
git commit -m "$(cat <<'EOF'
#27 feat: redesign org detail layout — status badge in header, hierarchy section, metadata line, roles filter

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Org search typeahead

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/partials/_search_results.html`
- Create: `tests/api/admin/test_orgs_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/admin/test_orgs_search.py
"""Integration tests for org search typeahead endpoint."""

import asyncio
import os
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.db import apply_schema, generate_id
import asyncpg

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def search_org_id():
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Searchable Org', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_search_returns_matching_org(client, search_org_id):
    response = client.get("/admin/orgs/search/?q=Searchable", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Searchable Org" in response.text


def test_search_excludes_archived_org(client):
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", oid
            )
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Archived Searchable', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get(
            "/admin/orgs/search/?q=Archived+Searchable", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Archived Searchable" not in response.text
    finally:
        asyncio.run(teardown())


def test_search_empty_query_returns_empty(client):
    response = client.get("/admin/orgs/search/?q=", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<li" not in response.text
```

- [ ] **Step 2: Run — confirm FAIL**

```bash
uv run pytest tests/api/admin/test_orgs_search.py -v -m integration
```

- [ ] **Step 3: Add `GET /orgs/search/` route to `orgs.py`**

```python
@router.get("/search/")
async def orgs_search(
    request: Request,
    q: str = "",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns an HTML fragment of matching org options."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT o.id, dn.display_name
               FROM organizations o
               LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
               WHERE o.archived_at IS NULL
                 AND dn.display_name ILIKE $1
               ORDER BY dn.display_name NULLS LAST
               LIMIT 20""",
            f"%{q.strip()}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_search_results.html",
        {"results": results},
    )
```

- [ ] **Step 4: Create `_search_results.html`**

```html
{# admin/orgs/partials/_search_results.html #}
{% for r in results %}
<li role="option"
    data-id="{{ r.id }}"
    data-label="{{ r.display_name }}">{{ r.display_name }}</li>
{% endfor %}
```

- [ ] **Step 5: Run tests — confirm PASS**

```bash
uv run pytest tests/api/admin/test_orgs_search.py -v -m integration
```

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/orgs.py src/templates/admin/orgs/partials/_search_results.html \
        tests/api/admin/test_orgs_search.py
git commit -m "$(cat <<'EOF'
#27 feat: add org search typeahead endpoint

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Core fields inline editing (name, acronym, active, notes)

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/partials/_core_fields_read.html`
- Create: `src/templates/admin/orgs/partials/_core_fields_form.html`
- Create: `tests/api/admin/test_orgs_inline.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/admin/test_orgs_inline.py
"""Integration tests for org core fields inline editing."""

import asyncio, os
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.db import apply_schema, generate_id
import asyncpg

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_id():
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Inline Test Org', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_core_fields_get_returns_partial(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/core/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Inline Test Org" in r.text


def test_core_fields_post_updates_name(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/core/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "acronym": "", "active": "true", "notes": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_core_fields_post_missing_name_returns_form(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/core/",
        headers=HTMX_HEADERS,
        data={"name": "", "acronym": "", "active": "true", "notes": ""},
    )
    assert r.status_code == 422 or "required" in r.text.lower()
```

- [ ] **Step 2: Run — confirm FAIL**

```bash
uv run pytest tests/api/admin/test_orgs_inline.py -v -m integration
```

- [ ] **Step 3: Add `GET/POST /inline/core/` routes to `orgs.py`**

```python
@router.get("/{org_id}/inline/core/")
async def org_inline_core_get(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for core org fields."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    canonical = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    acronym_row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    ctx = {
        "org": org,
        "canonical_name": canonical["name"] if canonical else "",
        "canonical_acronym": acronym_row["acronym"] if acronym_row else "",
    }
    return templates.TemplateResponse(request, "admin/orgs/partials/_core_fields_read.html", ctx)


@router.post("/{org_id}/inline/core/")
async def org_inline_core_post(
    org_id: str,
    request: Request,
    name: str = Form(...),
    acronym: str = Form(""),
    active: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save core org fields inline; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if not name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    async with db.transaction():
        await db.execute(
            "UPDATE organizations SET active=$1, notes=$2 WHERE id=$3",
            active == "true", notes.strip() or None, org_id,
        )
        existing = await db.fetchrow(
            "SELECT id FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
            org_id,
        )
        if existing:
            await db.execute(
                "UPDATE organization_names SET name=$1 WHERE id=$2", name.strip(), existing["id"]
            )
        else:
            await db.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(), org_id, name.strip(),
            )
        acronym_stripped = acronym.strip()
        existing_acronym = await db.fetchrow(
            "SELECT id FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
            org_id,
        )
        if acronym_stripped:
            if existing_acronym:
                await db.execute(
                    "UPDATE organization_acronyms SET acronym=$1 WHERE id=$2",
                    acronym_stripped, existing_acronym["id"],
                )
            else:
                await db.execute(
                    "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), org_id, acronym_stripped,
                )
        elif existing_acronym:
            await db.execute(
                "DELETE FROM organization_acronyms WHERE id=$1", existing_acronym["id"]
            )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    canonical = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    acronym_row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    ctx = {
        "org": org,
        "canonical_name": canonical["name"] if canonical else "",
        "canonical_acronym": acronym_row["acronym"] if acronym_row else "",
    }
    return templates.TemplateResponse(request, "admin/orgs/partials/_core_fields_read.html", ctx)
```

- [ ] **Step 4: Create `_core_fields_read.html` and `_core_fields_form.html`**

`_core_fields_read.html` — shows current values with edit button wiring:

```html
{# admin/orgs/partials/_core_fields_read.html #}
<dl class="detail-grid">
  <dt>Name</dt>
  <dd>{{ canonical_name or '(unnamed)' }}{% if canonical_acronym %} ({{ canonical_acronym }}){% endif %}</dd>
  {% if org.notes %}
  <dt>Notes</dt><dd>{{ org.notes }}</dd>
  {% endif %}
  <dt>Active</dt>
  <dd>{% if org.active %}Yes{% else %}No{% endif %}</dd>
</dl>
<button class="btn btn--sm btn--secondary"
        hx-get="/admin/orgs/{{ org.id }}/inline/core/"
        hx-target="#core-fields"
        hx-swap="innerHTML"
        hx-push-url="false"
        type="button">Edit</button>
```

`_core_fields_form.html` — inline form (GET returns this; the button in the read partial should swap to it):

```html
{# admin/orgs/partials/_core_fields_form.html #}
<form hx-post="/admin/orgs/{{ org.id }}/inline/core/"
      hx-target="#core-fields"
      hx-swap="innerHTML">
  <div class="field-group">
    <label for="cf-name">Name</label>
    <input id="cf-name" name="name" type="text" required value="{{ canonical_name }}">
  </div>
  <div class="field-group">
    <label for="cf-acronym">Acronym</label>
    <input id="cf-acronym" name="acronym" type="text" value="{{ canonical_acronym }}">
  </div>
  <div class="field-group">
    <label>
      <input type="checkbox" name="active" value="true"{% if org.active %} checked{% endif %}>
      Active
    </label>
  </div>
  <div class="field-group">
    <label for="cf-notes">Notes</label>
    <textarea id="cf-notes" name="notes">{{ org.notes or '' }}</textarea>
  </div>
  <div class="btn-group">
    <button type="submit" class="btn btn--primary btn--sm">Save</button>
    <button type="button" class="btn btn--secondary btn--sm"
            hx-get="/admin/orgs/{{ org.id }}/inline/core/"
            hx-target="#core-fields"
            hx-swap="innerHTML">Cancel</button>
  </div>
</form>
```

Add a `GET /inline/core/edit/` route that returns `_core_fields_form.html`, and update the Edit button in `_core_fields_read.html` to call it:

```python
@router.get("/{org_id}/inline/core/edit/")
async def org_inline_core_edit_get(
    org_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return the edit form partial for core org fields."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    canonical = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    acronym_row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        org_id,
    )
    ctx = {
        "org": org,
        "canonical_name": canonical["name"] if canonical else "",
        "canonical_acronym": acronym_row["acronym"] if acronym_row else "",
    }
    return templates.TemplateResponse(request, "admin/orgs/partials/_core_fields_form.html", ctx)
```

Update the Edit button in `_core_fields_read.html` to call this route:

```html
<button class="btn btn--sm btn--secondary"
        hx-get="/admin/orgs/{{ org.id }}/inline/core/edit/"
        hx-target="#core-fields"
        hx-swap="innerHTML"
        type="button">Edit</button>
```

Add a test for this route in `test_orgs_inline.py`:

```python
def test_core_fields_edit_get_returns_form(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/core/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text
    assert "Inline Test Org" in r.text
```

- [ ] **Step 5: Run tests — confirm PASS**

```bash
uv run pytest tests/api/admin/test_orgs_inline.py -v -m integration
```

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/orgs.py \
        src/templates/admin/orgs/partials/_core_fields_read.html \
        src/templates/admin/orgs/partials/_core_fields_form.html \
        tests/api/admin/test_orgs_inline.py
git commit -m "$(cat <<'EOF'
#27 feat: inline editing for core org fields (name, acronym, active, notes)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Parent org inline editing

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/partials/_parent_read.html`
- Create: `src/templates/admin/orgs/partials/_parent_form.html`
- Modify: `tests/api/admin/test_orgs_inline.py`

- [ ] **Step 1: Add tests for parent inline editing to `test_orgs_inline.py`**

```python
def test_parent_get_returns_partial(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/parent/", headers=HTMX_HEADERS)
    assert r.status_code == 200

def test_parent_post_sets_parent(client, org_id):
    # Create a second org to be the parent
    dsn = _dsn()
    parent_id = generate_id()
    async def make_parent():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
        finally:
            await conn.close()
    async def drop_parent():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", parent_id)
        finally:
            await conn.close()
    asyncio.run(make_parent())
    try:
        r = client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": parent_id},
            follow_redirects=False,
        )
        assert r.status_code == 200
    finally:
        # Clear parent before dropping
        client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": ""},
        )
        asyncio.run(drop_parent())

def test_parent_post_circular_returns_422(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        headers=HTMX_HEADERS,
        data={"parent_id": org_id},  # self-reference
        follow_redirects=False,
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run — confirm FAIL**

```bash
uv run pytest tests/api/admin/test_orgs_inline.py::test_parent_get_returns_partial -v -m integration
```

- [ ] **Step 3: Add `GET/POST /inline/parent/` routes to `orgs.py`**

```python
@router.get("/{org_id}/inline/parent/")
async def org_inline_parent_get(
    org_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1", org["parent_id"]
        )
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_parent_read.html", {"org": org, "parent": parent}
    )


@router.post("/{org_id}/inline/parent/")
async def org_inline_parent_post(
    org_id: str, request: Request,
    parent_id: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if parent_id and parent_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own parent")
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404)
    resolved = parent_id.strip() or None
    if resolved:
        exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
        if not exists:
            raise HTTPException(status_code=422, detail="Parent organization not found")
    await db.execute(
        "UPDATE organizations SET parent_id=$1 WHERE id=$2", resolved, org_id
    )
    org = await db.fetchrow("SELECT * FROM organizations WHERE id=$1", org_id)
    parent = None
    if org["parent_id"]:
        parent = await db.fetchrow(
            "SELECT o.id, dn.display_name FROM organizations o"
            " LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id"
            " WHERE o.id=$1", org["parent_id"]
        )
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_parent_read.html", {"org": org, "parent": parent}
    )
```

- [ ] **Step 4: Create `_parent_read.html` and `_parent_form.html`**

`_parent_read.html`:
```html
{# admin/orgs/partials/_parent_read.html #}
<div id="parent-section">
  <strong>Parent Organization</strong>
  {% if parent %}
  <a href="/admin/orgs/{{ parent.id }}/">{{ parent.display_name or parent.id }}</a>
  {% else %}
  <span style="color:var(--color-text-muted)">None</span>
  {% endif %}
  <button type="button" class="btn btn--sm btn--secondary"
          hx-get="/admin/orgs/{{ org.id }}/inline/parent/edit/"
          hx-target="#parent-section"
          hx-swap="outerHTML">Change</button>
</div>
```

`_parent_form.html` — uses combobox pattern:
```html
{# admin/orgs/partials/_parent_form.html #}
<form id="parent-section"
      hx-post="/admin/orgs/{{ org.id }}/inline/parent/"
      hx-target="#parent-section"
      hx-swap="outerHTML">
  <label for="parent-search">Parent Organization</label>
  <div style="position:relative">
    <input id="parent-search" type="text" autocomplete="off"
           placeholder="Type to search…"
           value="{{ parent.display_name if parent else '' }}"
           hx-get="/admin/orgs/search/"
           hx-trigger="input changed delay:200ms"
           hx-target="#parent-search-results"
           hx-params="q"
           name="parent-search-display">
    <input type="hidden" name="parent_id" id="parent-id-hidden"
           value="{{ parent.id if parent else '' }}">
    <ul id="parent-search-results" role="listbox"
        style="position:absolute;background:var(--color-surface-1);border:1px solid var(--color-border);list-style:none;margin:0;padding:0;width:100%;z-index:10"></ul>
  </div>
  <div class="btn-group" style="margin-top:var(--space-2)">
    <button type="submit" class="btn btn--primary btn--sm">Save</button>
    <button type="button" class="btn btn--secondary btn--sm"
            hx-get="/admin/orgs/{{ org.id }}/inline/parent/"
            hx-target="#parent-section"
            hx-swap="outerHTML">Cancel</button>
    <button type="submit" name="parent_id" value=""
            class="btn btn--secondary btn--sm">Clear parent</button>
  </div>
</form>
<script>
  // Wire combobox: clicking a result sets the hidden input and display input
  document.getElementById('parent-search-results').addEventListener('click', function(e) {
    const li = e.target.closest('[data-id]');
    if (!li) return;
    document.getElementById('parent-id-hidden').value = li.dataset.id;
    document.getElementById('parent-search').value = li.dataset.label;
    this.innerHTML = '';
  });
</script>
```

Add a `GET /{org_id}/inline/parent/edit/` route that returns `_parent_form.html`.

- [ ] **Step 5: Run tests — confirm PASS**

```bash
uv run pytest tests/api/admin/test_orgs_inline.py -v -m integration
```

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/orgs.py \
        src/templates/admin/orgs/partials/_parent_read.html \
        src/templates/admin/orgs/partials/_parent_form.html \
        tests/api/admin/test_orgs_inline.py
git commit -m "$(cat <<'EOF'
#27 feat: inline parent org editing with HTMX combobox

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Names CRUD

**Files:**
- Create: `src/api/admin/orgs_names.py`
- Modify: `src/api/admin/router.py`
- Create: `src/templates/admin/orgs/partials/_name_row.html`
- Create: `src/templates/admin/orgs/partials/_name_form_row.html`
- Create: `tests/api/admin/test_orgs_names.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/admin/test_orgs_names.py
"""Integration tests for org names CRUD."""

import asyncio, os
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.db import apply_schema, generate_id
import asyncpg

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_and_name():
    dsn = _dsn()
    oid, nid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Original Name', TRUE)",
                nid, oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, nid
    asyncio.run(teardown())


def test_names_new_row_returns_form(client, org_and_name):
    oid, _ = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_names_create(client, org_and_name):
    oid, _ = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "DBA Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "DBA Name" in r.text


def test_names_edit_row_returns_form(client, org_and_name):
    oid, nid = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


def test_names_update(client, org_and_name):
    oid, nid = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_names_delete(client, org_and_name):
    dsn = _dsn()
    oid, _ = org_and_name
    # Create a non-canonical name to delete
    nid2 = generate_id()
    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Former Name', FALSE)",
                nid2, oid,
            )
        finally:
            await conn.close()
    asyncio.run(add())
    r = client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_names_delete_unknown_returns_404(client, org_and_name):
    oid, _ = org_and_name
    r = client.delete(f"/admin/orgs/{oid}/names/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
```

- [ ] **Step 2: Run — confirm FAIL**

```bash
uv run pytest tests/api/admin/test_orgs_names.py -v -m integration
```

- [ ] **Step 3: Create `src/api/admin/orgs_names.py`**

```python
"""Admin CRUD for organization names."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs/{org_id}/names", tags=["admin-org-names"])


async def _get_org_or_404(org_id: str, db):
    org = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.get("/new-row/")
async def name_new_row(
    org_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_name_form_row.html",
        {"org_id": org_id, "name": None},
    )


@router.post("/")
async def name_create(
    org_id: str, request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5)",
        nid, org_id, name.strip(), name_type, is_canonical == "true",
    )
    row = await db.fetchrow("SELECT * FROM organization_names WHERE id=$1", nid)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_name_row.html", {"org_id": org_id, "n": row}
    )


@router.get("/{name_id}/edit-row/")
async def name_edit_row_get(
    org_id: str, name_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    name = await db.fetchrow(
        "SELECT * FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id, org_id,
    )
    if not name:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_name_form_row.html",
        {"org_id": org_id, "n": name},
    )


@router.post("/{name_id}/edit-row/")
async def name_edit_row_post(
    org_id: str, name_id: str, request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT * FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id, org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE organization_names SET name=$1, name_type=$2, is_canonical=$3 WHERE id=$4",
        name.strip(), name_type, is_canonical == "true", name_id,
    )
    row = await db.fetchrow("SELECT * FROM organization_names WHERE id=$1", name_id)
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_name_row.html", {"org_id": org_id, "n": row}
    )


@router.delete("/{name_id}/")
async def name_delete(
    org_id: str, name_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM organization_names WHERE id=$1 AND organization_id=$2",
        name_id, org_id,
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM organization_names WHERE id=$1", name_id)
    return HTMLResponse(content="", status_code=200)


def _is_htmx(request: Request) -> bool:
    return bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))
```

- [ ] **Step 4: Mount router in `router.py`**

```python
# Add import:
from src.api.admin import orgs_names as orgs_names_module

# Add include after the existing orgs router:
admin_router.include_router(orgs_names_module.router)
```

- [ ] **Step 5: Create `_name_row.html` and `_name_form_row.html`**

`_name_row.html`:
```html
{# admin/orgs/partials/_name_row.html — inline name read row #}
<tr id="name-row-{{ n.id }}">
  <td>{{ n.name }}</td>
  <td>{{ n.name_type }}</td>
  <td>{% if n.is_canonical %}<span class="badge badge--active">Yes</span>{% else %}—{% endif %}</td>
  <td>
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/orgs/{{ org_id }}/names/{{ n.id }}/edit-row/"
            hx-target="#name-row-{{ n.id }}"
            hx-swap="outerHTML">Edit</button>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/orgs/{{ org_id }}/names/{{ n.id }}/"
            hx-target="#name-row-{{ n.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete this name?">Delete</button>
  </td>
</tr>
```

`_name_form_row.html`:
```html
{# admin/orgs/partials/_name_form_row.html — inline name edit/new form row #}
<tr id="{% if n %}name-row-{{ n.id }}{% else %}name-row-new{% endif %}">
  <td colspan="4">
    <form {% if n %}
          hx-post="/admin/orgs/{{ org_id }}/names/{{ n.id }}/edit-row/"
          hx-target="#name-row-{{ n.id }}"
          {% else %}
          hx-post="/admin/orgs/{{ org_id }}/names/"
          hx-target="#name-row-new"
          {% endif %}
          hx-swap="outerHTML"
          style="display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap">
      <input type="text" name="name" required value="{{ n.name if n else '' }}" placeholder="Name">
      <select name="name_type">
        {% for t in ('legal', 'dba', 'former') %}
        <option value="{{ t }}"{% if n and n.name_type == t %} selected{% endif %}>{{ t }}</option>
        {% endfor %}
      </select>
      <label><input type="checkbox" name="is_canonical" value="true"
                    {% if n and n.is_canonical %} checked{% endif %}> Canonical</label>
      <button type="submit" class="btn btn--sm btn--primary">Save</button>
      <button type="button" class="btn btn--sm btn--secondary"
              {% if n %}
              hx-get="/admin/orgs/{{ org_id }}/names/{{ n.id }}/edit-row/"
              hx-target="#name-row-{{ n.id }}"
              {% else %}
              hx-get="/admin/orgs/{{ org_id }}/names/new-row/"
              hx-target="#name-row-new"
              {% endif %}
              hx-swap="delete">Cancel</button>
    </form>
  </td>
</tr>
```

- [ ] **Step 6: Run tests — confirm PASS**

```bash
uv run pytest tests/api/admin/test_orgs_names.py -v -m integration
```

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/orgs_names.py src/api/admin/router.py \
        src/templates/admin/orgs/partials/_name_row.html \
        src/templates/admin/orgs/partials/_name_form_row.html \
        tests/api/admin/test_orgs_names.py
git commit -m "$(cat <<'EOF'
#27 feat: inline CRUD for org names

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Addresses CRUD

**Files:**
- Create: `src/api/admin/orgs_addresses.py`
- Modify: `src/api/admin/router.py`
- Create: `src/templates/admin/orgs/partials/_address_row.html`
- Create: `src/templates/admin/orgs/partials/_address_form_row.html`
- Create: `tests/api/admin/test_orgs_addresses.py`

Follows the same pattern as Task 8. Key differences:

- Addresses are stored in two tables: `addresses` (the canonical address record) and `entity_addresses` (the polymorphic join). Creating an address requires inserting into both.
- `address_type` is `CHECK (address_type IN ('mailing', 'physical', 'other'))`.
- Display: show `standardized` if set, else `address_line_1 || city || region || postal_code`.
- The `address_type` select in the form has options: `mailing`, `physical`, `other`.
- The `_address_row.html` should render the one-line address summary.
- On create: `INSERT INTO addresses (id, address_line_1, city, region, postal_code) VALUES ...` then `INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type) VALUES ...`. The detail query for the form joins both tables.
- On delete: delete from `entity_addresses` only (preserve the address record for other potential references).

Test cases to cover: create, edit-row GET (returns form with existing values), edit-row POST (updates), delete (removes entity_addresses row).

- [ ] **Step 1: Write `tests/api/admin/test_orgs_addresses.py`** (mirror Task 8 test structure)
- [ ] **Step 2: Run — confirm FAIL**
- [ ] **Step 3: Create `src/api/admin/orgs_addresses.py`**
- [ ] **Step 4: Mount router in `router.py`**
- [ ] **Step 5: Create `_address_row.html` and `_address_form_row.html`**
- [ ] **Step 6: Run — confirm PASS**
- [ ] **Step 7: Commit** (`#27 feat: inline CRUD for org addresses`)

---

## Task 10: Contact Methods CRUD

**Files:**
- Create: `src/api/admin/orgs_contacts.py`
- Modify: `src/api/admin/router.py`
- Create: `src/templates/admin/orgs/partials/_contact_row.html`
- Create: `src/templates/admin/orgs/partials/_contact_form_row.html`
- Create: `tests/api/admin/test_orgs_contacts.py`

Same pattern as Task 8. Key differences:

- Table: `contact_methods` with `entity_type`, `entity_id`, `contact_type`, `value`.
- `contact_type` has no DB CHECK constraint — use a reasonable set in the form: `phone`, `email`, `fax`, `other`.
- `value` is the phone/email/fax string. Display as-is in the read row.

- [ ] **Step 1: Write tests** (create, edit-row GET/POST, delete, 404 on unknown)
- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Create `orgs_contacts.py`**
- [ ] **Step 4: Mount in `router.py`**
- [ ] **Step 5: Create partials**
- [ ] **Step 6: Run — PASS**
- [ ] **Step 7: Commit** (`#27 feat: inline CRUD for org contacts`)

---

## Task 11: Links CRUD

**Files:**
- Create: `src/api/admin/orgs_links.py`
- Modify: `src/api/admin/router.py`
- Create: `src/templates/admin/orgs/partials/_link_row.html`
- Create: `src/templates/admin/orgs/partials/_link_form_row.html`
- Create: `tests/api/admin/test_orgs_links.py`

Same pattern as Task 8. Key differences:

- Table: `links` with `link_type_id`, `is_active`, `is_canonical`.
- The form needs a `<select>` for `link_type_id` — fetch all `link_types` rows and group as `<optgroup label="Social">` (is_social=TRUE) and `<optgroup label="General">` (is_social=FALSE).
- `_link_row.html`: render `url` as a hyperlink `<a href="{{ l.url }}" target="_blank" rel="noopener noreferrer">{{ l.url }}</a>`; show `link_type_name`; show `is_active` status.
- On delete: `DELETE FROM links WHERE id=$1 AND entity_type='organization' AND entity_id=$2`.

Test cases: create a link, verify it appears; edit url/type/is_active; delete; 404 on unknown.

- [ ] **Step 1: Write `tests/api/admin/test_orgs_links.py`**
- [ ] **Step 2: Run — FAIL**
- [ ] **Step 3: Create `orgs_links.py`** (routes load `link_types` for the form `<select>`)
- [ ] **Step 4: Mount in `router.py`**
- [ ] **Step 5: Create partials** (include `<optgroup>` grouping in form)
- [ ] **Step 6: Run — PASS**
- [ ] **Step 7: Commit** (`#27 feat: inline CRUD for org links`)

---

## Task 12: Identifiers CRUD

**Files:**
- Create: `src/api/admin/orgs_identifiers.py`
- Modify: `src/api/admin/router.py`
- Create: `src/templates/admin/orgs/partials/_identifier_row.html`
- Create: `src/templates/admin/orgs/partials/_identifier_form_row.html`
- Create: `tests/api/admin/test_orgs_identifiers.py`

Same pattern as Task 8. Key differences:

- Table: `identifiers` with `entity_identifier_type_id`, `value`.
- Form `<select>`: fetch `entity_identifier_types WHERE entity_type='organization'` for the type options.
- `_identifier_row.html`: show `type_name` (short) with `title="{{ type_full_name }}"` for hover tooltip; show `value`.
- On delete: `DELETE FROM identifiers WHERE id=$1 AND entity_id=$2`.

- [ ] **Step 1–7:** Same structure as Task 8. Commit: `#27 feat: inline CRUD for org identifiers`

---

## Task 13: Hierarchy — children CRUD

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/partials/_child_row.html`
- Create: `src/templates/admin/orgs/partials/_child_form_row.html`
- Create: `tests/api/admin/test_orgs_children.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/admin/test_orgs_children.py
"""Integration tests for org hierarchy children CRUD."""

import asyncio, os
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.db import apply_schema, generate_id
import asyncpg

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}

def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def parent_and_child():
    dsn = _dsn()
    pid, cid = generate_id(), generate_id()
    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", pid)
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", cid)
        finally:
            await conn.close()
    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET parent_id=NULL WHERE parent_id=$1 OR id=$1", pid
            )
            await conn.execute("DELETE FROM organizations WHERE id=$1", cid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", pid)
        finally:
            await conn.close()
    asyncio.run(setup())
    yield pid, cid
    asyncio.run(teardown())


def test_add_child_sets_parent_id(client, parent_and_child):
    pid, cid = parent_and_child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": cid},
    )
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(_dsn())
        await apply_schema(conn)
        try:
            row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
            assert row["parent_id"] == pid
        finally:
            await conn.close()
    asyncio.run(check())


def test_remove_child_clears_parent_id(client, parent_and_child):
    pid, cid = parent_and_child
    # First set the parent
    client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    # Now unlink
    r = client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(_dsn())
        await apply_schema(conn)
        try:
            row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
            assert row["parent_id"] is None
        finally:
            await conn.close()
    asyncio.run(check())


def test_circular_child_returns_422(client, parent_and_child):
    pid, _ = parent_and_child
    # Try to make pid its own child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": pid},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run — FAIL**

```bash
uv run pytest tests/api/admin/test_orgs_children.py -v -m integration
```

- [ ] **Step 3: Add children routes to `orgs.py`**

```python
@router.get("/{org_id}/children/new-row/")
async def children_new_row(
    org_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_child_form_row.html", {"org_id": org_id}
    )


@router.post("/{org_id}/children/")
async def children_add(
    org_id: str, request: Request,
    child_id: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    if child_id == org_id:
        raise HTTPException(status_code=422, detail="An organization cannot be its own child")
    child = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", child_id)
    if not child:
        raise HTTPException(status_code=422, detail="Child organization not found")
    await db.execute("UPDATE organizations SET parent_id=$1 WHERE id=$2", org_id, child_id)
    row = await db.fetchrow(
        """SELECT o.id, o.active, o.archived_at, dn.display_name AS canonical_name
           FROM organizations o
           LEFT JOIN v_org_display_names dn ON dn.organization_id=o.id
           WHERE o.id=$1""",
        child_id,
    )
    if not _is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request, "admin/orgs/partials/_child_row.html", {"org_id": org_id, "child": row}
    )


@router.delete("/{org_id}/children/{child_id}/")
async def children_remove(
    org_id: str, child_id: str, request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    child = await db.fetchrow(
        "SELECT id FROM organizations WHERE id=$1 AND parent_id=$2", child_id, org_id
    )
    if not child:
        raise HTTPException(status_code=404)
    await db.execute("UPDATE organizations SET parent_id=NULL WHERE id=$1", child_id)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content="", status_code=200)
```

- [ ] **Step 4: Create `_child_row.html` and `_child_form_row.html`**

`_child_row.html`:
```html
<tr id="child-row-{{ child.id }}">
  <td><a href="/admin/orgs/{{ child.id }}/">{{ child.canonical_name or '(unnamed)' }}</a></td>
  <td>
    {% if child.archived_at %}<span class="badge badge--archived">Archived</span>
    {% elif not child.active %}<span class="badge badge--inactive">Inactive</span>
    {% else %}<span class="badge badge--active">Active</span>{% endif %}
  </td>
  <td>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/orgs/{{ org_id }}/children/{{ child.id }}/"
            hx-target="#child-row-{{ child.id }}"
            hx-swap="outerHTML"
            hx-confirm="Remove {{ child.canonical_name or child.id }} as a child?">
      Unlink
    </button>
  </td>
</tr>
```

`_child_form_row.html` — combobox to select an existing org:
```html
<tr id="child-row-new">
  <td colspan="3">
    <form hx-post="/admin/orgs/{{ org_id }}/children/"
          hx-target="#children-table tbody"
          hx-swap="afterbegin"
          style="display:flex;gap:var(--space-2);align-items:center">
      <div style="position:relative;flex:1">
        <input type="text" autocomplete="off" placeholder="Search for an organization…"
               hx-get="/admin/orgs/search/"
               hx-trigger="input changed delay:200ms"
               hx-target="#child-search-results"
               hx-params="q"
               name="child-display">
        <input type="hidden" name="child_id" id="child-id-hidden">
        <ul id="child-search-results" role="listbox"
            style="position:absolute;background:var(--color-surface-1);border:1px solid var(--color-border);list-style:none;margin:0;padding:0;width:100%;z-index:10"></ul>
      </div>
      <button type="submit" class="btn btn--sm btn--primary">Add</button>
      <button type="button" class="btn btn--sm btn--secondary"
              hx-target="#child-row-new" hx-swap="delete"
              hx-get="/admin/orgs/{{ org_id }}/children/new-row/">Cancel</button>
    </form>
    <script>
      document.getElementById('child-search-results').addEventListener('click', function(e) {
        const li = e.target.closest('[data-id]');
        if (!li) return;
        document.getElementById('child-id-hidden').value = li.dataset.id;
        this.previousElementSibling.previousElementSibling.value = li.dataset.label;
        this.innerHTML = '';
      });
    </script>
  </td>
</tr>
```

- [ ] **Step 5: Run tests — confirm PASS**

```bash
uv run pytest tests/api/admin/test_orgs_children.py -v -m integration
```

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/orgs.py \
        src/templates/admin/orgs/partials/_child_row.html \
        src/templates/admin/orgs/partials/_child_form_row.html \
        tests/api/admin/test_orgs_children.py
git commit -m "$(cat <<'EOF'
#27 feat: inline hierarchy children CRUD (add/unlink child orgs)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Wire detail.html partials + remove `/edit/` routes

**Files:**
- Modify: `src/templates/admin/orgs/detail.html` (wire all HTMX attributes)
- Modify: `src/api/admin/orgs.py` (delete `org_edit_form` and `org_update`)
- Modify: `tests/api/admin/test_orgs.py` (update/remove /edit/ tests)

- [ ] **Step 1: Update `detail.html` to include all partials**

All sections created in Tasks 6–13 should be included in `detail.html` via `{% include %}` directives with correct HTMX attributes. Verify every table's "Add" button has `hx-get`, `hx-target`, and `hx-swap="afterbegin"`. Verify every row partial's Edit/Delete buttons use `hx-target="closest tr"` or the row's `id` attribute.

- [ ] **Step 2: Delete `org_edit_form` and `org_update` from `orgs.py`**

Remove the two route handlers:
- `GET /{org_id}/edit/` (`org_edit_form`)
- `POST /{org_id}/edit/` (`org_update`)

Also remove `src/templates/admin/orgs/form.html` if it is only used by these routes.

- [ ] **Step 3: Update tests — remove /edit/ tests, add 404 assertion**

In `tests/api/admin/test_orgs.py`:
- Remove: `test_edit_org_form_returns_200`, `test_edit_org_does_not_overwrite_acronym`, `test_edit_org_form_shows_existing_acronym`, `test_edit_org_insert_new_acronym`, `test_edit_org_update_existing_acronym`, `test_edit_org_clear_acronym`
- Add:

```python
def test_edit_route_removed(client):
    """GET /edit/ must return 404 — route has been deleted."""
    r = client.get(f"/admin/orgs/{generate_id()}/edit/", headers=AUTH_HEADERS)
    assert r.status_code == 404
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest -v -m integration
```
Expected: all pass.

- [ ] **Step 5: Run linter**

```bash
uv run ruff check .
```
Fix any issues.

- [ ] **Step 6: Commit**

```bash
git add src/api/admin/orgs.py src/templates/admin/orgs/ \
        tests/api/admin/test_orgs.py
git commit -m "$(cat <<'EOF'
#27 feat: wire detail page partials, remove /edit/ route

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Final verification

- [ ] **Step 1: Full test suite — clean run**

```bash
uv run pytest -v -m integration 2>&1 | tail -20
```
Expected: zero failures.

- [ ] **Step 2: Manual smoke test**

Start dev server and verify the WSLCB org page:
- Status badge appears in header next to name
- No ID at top of page
- Notes visible if present
- Hierarchy section shows parent/children with combobox Add
- Names/Addresses/Contacts/Links/Identifiers all have Add buttons and row-level Edit/Delete
- Links show `is_active` status; social links grouped by platform
- Roles filter works (type to filter visible rows)
- Metadata line at bottom shows ID, created, updated
- Danger Zone unchanged

- [ ] **Step 3: Linter final check**

```bash
uv run ruff check .
```

- [ ] **Step 4: Commit if any remaining changes, then push**

```bash
git push origin HEAD
```
