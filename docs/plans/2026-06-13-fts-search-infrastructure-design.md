# FTS Search Infrastructure Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

Replace ILIKE substring matching on entity names with PostgreSQL full-text search (FTS)
across the four core searchable entity types: organizations, people, roles, and jurisdictions.

Primary motivations:
- Accent-insensitive matching for person names (`Hernández` ↔ `hernandez`)
- Punctuation-normalizing matching (`Jr.` ↔ `Jr`)
- Word-boundary semantics for lookup queries (public API consumers)
- Performance via GIN indexes (vs. seq scans on name columns today)

## Approved Approach

### Text search configurations

Two named configs added to `schema.sql`:

| Config | Used by | Dictionary chain |
|---|---|---|
| `pm_simple` | orgs, roles, jurisdictions | `simple` (lowercase + punctuation-strip; no stemming, no stop words) |
| `pm_unaccent_simple` | people | `pm_unaccent → simple` (accent-strip first, then same as above) |

`simple` is chosen over `english` deliberately: org and role names are proper-noun corpora
where stemming produces wrong lexemes and stop-word removal drops meaningful tokens
("Department of Health", "Finance and Commerce").

`unaccent` is a built-in PostgreSQL extension with no external file maintenance burden.

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;

-- pm_simple: lowercase + punctuation-strip only
CREATE TEXT SEARCH CONFIGURATION pm_simple (COPY = simple);

-- pm_unaccent_simple: accent-strip, then pm_simple behaviour
CREATE TEXT SEARCH DICTIONARY pm_unaccent (
    TEMPLATE = unaccent,
    RULES    = 'unaccent'
);
CREATE TEXT SEARCH CONFIGURATION pm_unaccent_simple (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION pm_unaccent_simple
    ALTER MAPPING FOR hword, hword_part, word
    WITH pm_unaccent, simple;
```

All wrapped in `DO $$ IF NOT EXISTS ... $$` blocks matching existing schema migration style.

### New columns

One `tsvector` column on each parent entity table:

| Table | Column | Sources | Weights | Config |
|---|---|---|---|---|
| `organizations` | `search_tsv` | all `organization_names.name`, all `organization_acronyms.acronym`, `organizations.notes` | A / B / C | `pm_simple` |
| `people` | `search_tsv` | all `person_names.name WHERE visibility='public'`, `people.notes` | A / C | `pm_unaccent_simple` |
| `roles` | `search_tsv` | `roles.title`, `roles.notes` | A / B | `pm_simple` |
| `jurisdictions` | `search_tsv` | `jurisdictions.name`, `jurisdictions.slug`, `jurisdictions.notes` | A / B / C | `pm_simple` |

GIN index on each column:

```sql
CREATE INDEX idx_organizations_search_tsv ON organizations USING GIN (search_tsv);
CREATE INDEX idx_people_search_tsv        ON people        USING GIN (search_tsv);
CREATE INDEX idx_roles_search_tsv         ON roles         USING GIN (search_tsv);
CREATE INDEX idx_jurisdictions_search_tsv ON jurisdictions USING GIN (search_tsv);
```

### Trigger strategy

**Pattern A — single-table (roles, jurisdictions):** `search_tsv` depends only on the
row's own columns. `BEFORE INSERT OR UPDATE OF <cols>` trigger sets `NEW.search_tsv`
directly. No extra `UPDATE`; no recursion risk.

```sql
-- Example shape for roles:
CREATE OR REPLACE FUNCTION fn_roles_search_tsv() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('pm_simple', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('pm_simple', coalesce(NEW.notes,  '')), 'B');
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_roles_search_tsv
    BEFORE INSERT OR UPDATE OF title, notes ON roles
    FOR EACH ROW EXECUTE FUNCTION fn_roles_search_tsv();
```

Jurisdictions follow the same pattern (columns: `name`/A, `slug`/B, `notes`/C).

**Pattern B — multi-table aggregate (organizations, people):** `search_tsv` aggregates
across child rows. `AFTER` triggers on child tables issue a targeted `UPDATE` on the parent.
The parent's own notes change is handled by a column-specific `AFTER UPDATE OF notes`
trigger (column restriction prevents recursion when `search_tsv` itself is updated).

```sql
CREATE OR REPLACE FUNCTION fn_refresh_org_search_tsv() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id TEXT;
BEGIN
    v_id := coalesce(NEW.organization_id, OLD.organization_id);
    UPDATE organizations SET search_tsv = (
        SELECT
            setweight(to_tsvector('pm_simple', coalesce(string_agg(DISTINCT n.name,    ' '), '')), 'A') ||
            setweight(to_tsvector('pm_simple', coalesce(string_agg(DISTINCT a.acronym, ' '), '')), 'B') ||
            setweight(to_tsvector('pm_simple', coalesce(o.notes, '')),                             'C')
        FROM organizations o
        LEFT JOIN organization_names    n ON n.organization_id = o.id
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id
        WHERE o.id = v_id
        GROUP BY o.id, o.notes
    ) WHERE id = v_id;
    RETURN NULL;
END;
$$;
-- Fires when any name variant or acronym changes:
CREATE TRIGGER trg_org_names_search_tsv
    AFTER INSERT OR UPDATE OR DELETE ON organization_names
    FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv();
CREATE TRIGGER trg_org_acronyms_search_tsv
    AFTER INSERT OR UPDATE OR DELETE ON organization_acronyms
    FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv();
-- Fires when notes changes on the org row itself:
CREATE TRIGGER trg_org_notes_search_tsv
    AFTER UPDATE OF notes ON organizations
    FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv();
```

People follow the same structure; the aggregation query filters `AND pn.visibility = 'public'`.
Visibility changes on `person_names` must trigger a tsvector refresh — a name going from
`public` → `hidden` must disappear from search results.

Trigger inventory: **7 triggers on 6 tables** (3 orgs, 2 people, 1 roles, 1 jurisdictions).

### Query changes

**Public API** (`/api/v1/orgs/search`, `/api/v1/people/search`): replace ILIKE with FTS.

```sql
-- Before
WHERE n.name ILIKE $1 OR a.acronym ILIKE $1 OR ...

-- After
WHERE o.search_tsv @@ plainto_tsquery('pm_simple', $1)
ORDER BY ts_rank(o.search_tsv, plainto_tsquery('pm_simple', $1)) DESC, n.name NULLS LAST
```

`plainto_tsquery` is used (not `to_tsquery`) — it accepts arbitrary user input safely and
treats multi-word queries as AND.

**Admin list view filters** (`?q=` on orgs, people, roles, role_assignments): same
substitution; lower priority but infrastructure is in place.

**Admin typeaheads** (`/admin/orgs/search/`, `/admin/people/search/`, etc.): **keep ILIKE**.
Typeaheads are substring/prefix by user expectation ("approp" → "Appropriations Committee").
FTS is word-boundary and would regress this behaviour. Add trigram GIN on underlying name
columns to make ILIKE fast without changing query semantics:

```sql
CREATE INDEX idx_org_names_name_trgm    ON organization_names USING GIN (lower(name)  gin_trgm_ops);
CREATE INDEX idx_person_names_name_trgm ON person_names       USING GIN (lower(name)  gin_trgm_ops)
    WHERE visibility = 'public';
CREATE INDEX idx_roles_title_trgm       ON roles              USING GIN (lower(title) gin_trgm_ops);
```

### Backfill migration

After columns and triggers are added, explicit backfill queries populate `search_tsv` on all
existing rows. Pattern A tables (roles, jurisdictions) can use `UPDATE roles SET search_tsv =
fn_...()` or inline the expression; Pattern B tables (orgs, people) run the full aggregation
query once per entity.

### Out of scope

- `bcp47_locales` / `iso15924_scripts`: already have trigram GIN; admin-only; leave as-is
- `role_assignments.notes`: not a search target
- `identifiers.value`: exact lookup; FTS adds no value
- Synonym dictionaries (e.g. `dept` → `department`): deferred; requires out-of-repo file
  maintenance and the operational cost is not justified by current consumer needs

## Key decisions

| Decision | Rationale |
|---|---|
| `simple` not `english` | Proper-noun corpus; stemming and stop-word removal both harmful |
| `unaccent` for people only | Person names have diacritics; org/role/jurisdiction names mostly ASCII |
| `tsvector` on parent, not child | Single index, single GIN; triggers already exist for parent touch |
| Keep ILIKE for typeaheads | Substring/prefix semantics required; trigram GIN handles performance |
| `plainto_tsquery` not `to_tsquery` | Safe for arbitrary API consumer input |
| Notes included at weight C | Free text; lower weight ensures name/title matches rank higher |
