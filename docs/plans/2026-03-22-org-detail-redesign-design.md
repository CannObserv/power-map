# Org Detail Screen Redesign

**Date:** 2026-03-22
**Status:** Approved

---

## Goal

Redesign the organization detail screen to:
- Demote low-value metadata; surface useful fields prominently
- Replace the full-page `/edit` redirect with in-place HTMX editing throughout
- Add full CRUD for all associated records (names, addresses, contacts, links, identifiers) directly on the detail page
- Consolidate `urls`, `social_links`, `url_types`, and `platforms` into unified `links` and `link_types` tables
- Unify the parent + child org display into a single Hierarchy section
- Add a client-side filter to the Roles table; remove the ID column

---

## Approved Approach

### Layout & information architecture

Top to bottom:

1. **Page header** — org display name (`h1`) + status badge inline; no "Edit" button
2. **Core fields card** — canonical name + acronym, notes, active toggle; all inline-editable via HTMX pencil icons
3. **Hierarchy** — parent org (link, editable via combobox) + children table (link/unlink existing orgs)
4. **Names** — table with row-level Edit + Delete; "Add" opens a blank form row
5. **Addresses** — same pattern
6. **Contact Methods** — same pattern
7. **Links** — same pattern; unified URLs and platform links; `is_active` column visible; platform links distinguished by `link_types.is_social`
8. **Identifiers** — same pattern
9. **Roles** — client-side filter input above table; ID column removed; Title links to role detail
10. **Metadata** — single muted line: `ID · Created · Updated`; ID retained for power users but not prominent
11. **Danger Zone** — unchanged

### Editing interaction model

**Option A (row-level HTMX swap)** — selected.

- Pencil icon on each row → `hx-get` fetches an edit form partial; `hx-swap="outerHTML"` replaces the row
- Save → `hx-post` → returns updated read row partial
- Cancel → `hx-get` restores the read partial
- "Add" button → `hx-get` fetches a blank form row prepended to `<tbody>`
- Non-HTMX fallback: forms submit normally with `RedirectResponse` fallback

### Org selector (parent + link-child)

HTMX combobox pattern: text input + hidden `<input>` for the ID. Typing triggers `hx-get /admin/orgs/search/?q=…` which returns a small HTML fragment of matching org names. Too many records for a plain `<select>`.

---

## Data Model

### `link_types` — replaces `url_types` + `platforms`

```sql
CREATE TABLE link_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    is_social    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Seed rows:

| slug | display_name | is_social |
|---|---|---|
| twitter | Twitter / X | true |
| bluesky | Bluesky | true |
| linkedin | LinkedIn | true |
| mastodon | Mastodon | true |
| instagram | Instagram | true |
| facebook | Facebook | true |
| youtube | YouTube | true |
| flickr | Flickr | true |
| website | Official Website | false |
| profile | Profile | false |
| wa_pdc | WA Public Disclosure Commission | false |
| sec_form_d | SEC Form D | false |
| wikipedia | Wikipedia | false |
| google_drive | Google Drive | false |
| other | Other | false |

### `links` — replaces `urls` + `social_links`

```sql
CREATE TABLE links (
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
```

Single FK, no dual-nullable columns, no CHECK constraint. Social links queryable via:

```sql
SELECT l.* FROM links l
JOIN link_types lt ON lt.id = l.link_type_id
WHERE lt.is_social = TRUE;
```

### Migration (inside `apply_schema`, idempotent)

1. Create `link_types`, insert seed rows
2. Create `links`
3. Migrate `urls` → `links` (map `url_type_id` → `link_type_id` via slug match, `is_active = TRUE`)
4. Migrate `social_links` → `links` (map `platform_id` → `link_type_id` via slug match, `is_active = TRUE`)
5. Drop `urls`, `social_links`, `url_types`, `platforms`

---

## Route Surface

### New routes

```
GET/POST  /admin/orgs/{org_id}/inline/core/
GET/POST  /admin/orgs/{org_id}/inline/parent/
GET       /admin/orgs/search/                                    — typeahead fragment

GET       /admin/orgs/{org_id}/names/new-row/
POST      /admin/orgs/{org_id}/names/
GET       /admin/orgs/{org_id}/names/{name_id}/edit-row/
POST      /admin/orgs/{org_id}/names/{name_id}/edit-row/
DELETE    /admin/orgs/{org_id}/names/{name_id}/

GET       /admin/orgs/{org_id}/addresses/new-row/
POST      /admin/orgs/{org_id}/addresses/
GET       /admin/orgs/{org_id}/addresses/{addr_id}/edit-row/
POST      /admin/orgs/{org_id}/addresses/{addr_id}/edit-row/
DELETE    /admin/orgs/{org_id}/addresses/{addr_id}/

GET       /admin/orgs/{org_id}/contacts/new-row/
POST      /admin/orgs/{org_id}/contacts/
GET       /admin/orgs/{org_id}/contacts/{contact_id}/edit-row/
POST      /admin/orgs/{org_id}/contacts/{contact_id}/edit-row/
DELETE    /admin/orgs/{org_id}/contacts/{contact_id}/

GET       /admin/orgs/{org_id}/links/new-row/
POST      /admin/orgs/{org_id}/links/
GET       /admin/orgs/{org_id}/links/{link_id}/edit-row/
POST      /admin/orgs/{org_id}/links/{link_id}/edit-row/
DELETE    /admin/orgs/{org_id}/links/{link_id}/

GET       /admin/orgs/{org_id}/identifiers/new-row/
POST      /admin/orgs/{org_id}/identifiers/
GET       /admin/orgs/{org_id}/identifiers/{ident_id}/edit-row/
POST      /admin/orgs/{org_id}/identifiers/{ident_id}/edit-row/
DELETE    /admin/orgs/{org_id}/identifiers/{ident_id}/

POST      /admin/orgs/{org_id}/children/
DELETE    /admin/orgs/{org_id}/children/{child_id}/
```

### Removed routes

```
GET   /admin/orgs/{org_id}/edit/   — deleted; superseded by inline routes
POST  /admin/orgs/{org_id}/edit/   — deleted
```

---

## Testing Strategy

| Area | Approach |
|---|---|
| Migration | Integration: existing `urls` + `social_links` rows land correctly in `links`; `apply_schema` idempotent |
| `link_types` seed | Unit: all 15 slugs present with correct `is_social` values |
| Social link query | Integration: `JOIN link_types WHERE is_social = TRUE` returns only platform links |
| Inline core fields | Integration: GET returns partial, POST updates DB and returns partial, invalid input returns form with error |
| Associated CRUD (×5) | Integration per section: create, read-row, update, delete happy paths; 404 on unknown item |
| Org search typeahead | Integration: matching orgs returned; archived orgs excluded; empty query returns empty fragment |
| Hierarchy | Integration: POST sets `parent_id`; DELETE clears it; circular parent returns 422 |
| Roles filter | Template check: filter `<input>` and `data-title` attributes present (pure client JS, no server test) |
| Removed routes | Assert `GET /orgs/{org_id}/edit/` returns 404 |

---

## Out of Scope

- People detail screen redesign (parallel work, separate issue)
- Bulk editing of associated records
- Roles CRUD from the org detail page (roles have their own detail screen)
- Pagination of associated records (none currently have enough rows to warrant it)
- API (non-admin) exposure of the `links` table
