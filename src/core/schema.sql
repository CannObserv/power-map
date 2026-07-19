-- Power Map — canonical DDL
-- All PKs are ULIDs (TEXT). All timestamps are TIMESTAMPTZ.
-- Requires PostgreSQL 15+ (NULLS NOT DISTINCT on unique indexes).
-- Apply with: psql -f schema.sql

-- Enable trigram similarity for duplicate detection
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- Lookup / Reference Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS link_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    is_social    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Each row is a (entity_type × identifier_type) pairing.
-- slug is globally unique; 'org_wa_pdc' and 'person_wa_pdc' are distinct entries.
-- Identifiers attach to role_assignments, not to role definitions — 'role' excluded.
CREATE TABLE IF NOT EXISTS entity_identifier_types (
    id           TEXT        PRIMARY KEY,
    entity_type  TEXT        NOT NULL
                             CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction')),
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,           -- short: "UBI", "SSN", "WA PDC"
    full_name    TEXT        NOT NULL,           -- long:  "Washington Unified Business Identifier"
    is_internal  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS entity_event_types (
    id                      TEXT PRIMARY KEY,
    slug                    TEXT UNIQUE NOT NULL,
    display_name            TEXT NOT NULL,
    applies_to              TEXT NOT NULL
                            CHECK (applies_to IN ('person', 'organization', 'both')),
    requires_year           BOOLEAN NOT NULL DEFAULT FALSE,
    requires_linked_entity  BOOLEAN NOT NULL DEFAULT FALSE,
    constraints             JSONB,   -- reserved: per-type validation rules (future use)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Jurisdiction Lookup Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS jurisdiction_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Relationship types for the jurisdiction graph.
-- category: spatial | governance | functional | lineage
-- symmetric: TRUE means querying (from_id=$id OR to_id=$id) is correct at
--   application level — the edge exists once in the DB, reads both ways.
CREATE TABLE IF NOT EXISTS jurisdiction_relationship_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    category     TEXT        NOT NULL
                             CHECK (category IN ('spatial', 'governance', 'functional', 'lineage')),
    is_symmetric BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- BCP 47 / ISO 15924 lookup tables (issue #123, Phase 2-prep)
-- Seeded by scripts/seed_locales_scripts.py from langcodes + pycountry.
-- Validation source for person_names.locale and person_names.script.
-- pg_trgm GIN indexes power the typeahead's substring search.
-- =============================================================================

CREATE TABLE IF NOT EXISTS bcp47_locales (
    code         TEXT        PRIMARY KEY,
    language     TEXT        NOT NULL,
    script       TEXT,
    region       TEXT,
    display_name TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bcp47_locales_code_trgm
    ON bcp47_locales USING GIN (code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_bcp47_locales_display_name_trgm
    ON bcp47_locales USING GIN (display_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS iso15924_scripts (
    code         TEXT        PRIMARY KEY,
    numeric_code SMALLINT    NOT NULL UNIQUE,
    name         TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_iso15924_scripts_code_trgm
    ON iso15924_scripts USING GIN (code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_iso15924_scripts_name_trgm
    ON iso15924_scripts USING GIN (name gin_trgm_ops);

-- =============================================================================
-- Addresses
-- =============================================================================

CREATE TABLE IF NOT EXISTS addresses (
    id             TEXT             PRIMARY KEY,
    raw_input      TEXT,                         -- original string before standardization
    standardized   TEXT,                         -- single-line form returned by API
    address_line_1 TEXT,
    address_line_2 TEXT,
    city           TEXT,
    region         TEXT,                         -- state / province
    postal_code    TEXT,
    country        TEXT             NOT NULL DEFAULT 'US',
    latitude       DOUBLE PRECISION,
    longitude      DOUBLE PRECISION,
    components     JSONB,
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- Polymorphic join: links any entity to one or more addresses with a typed relationship.
-- valid_from/valid_until (#181): valid-time window; NULL = open-ended on that side.
-- Overlapping windows are legitimate (mailing + physical simultaneously; two offices).
CREATE TABLE IF NOT EXISTS entity_addresses (
    id           TEXT        PRIMARY KEY,
    entity_type  TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person', 'role', 'jurisdiction')),
    entity_id    TEXT        NOT NULL,
    address_id   TEXT        NOT NULL REFERENCES addresses(id),
    address_type TEXT        NOT NULL CHECK (address_type IN ('mailing', 'physical', 'other')),
    display_name TEXT,                           -- optional label, e.g. "Seattle Office"
    valid_from   DATE,
    valid_until  DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ea_validity
        CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until)
);

CREATE INDEX IF NOT EXISTS idx_entity_addresses_entity
    ON entity_addresses(entity_type, entity_id);

-- Typed association between an organization and a jurisdiction.
-- display_name is a verb phrase for SVO rendering:
--   "{org} {display_name} {jurisdiction}" → "WA Legislature is governed by Washington State"
CREATE TABLE IF NOT EXISTS organization_jurisdiction_affiliation_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Typed office/kind classifier for roles (#261). Lets "all Representatives",
-- "all Senators" aggregate reliably across districts/chambers/states without
-- matching free-text titles. FK'd from roles.role_type_id (added by migration).
CREATE TABLE IF NOT EXISTS role_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    -- Advisory intent (#268): a producer should attach this office with a
    -- jurisdiction (structural-tuple match). A hint for producers, not enforced
    -- by resolve_role.
    expects_jurisdiction BOOLEAN NOT NULL DEFAULT FALSE,
    -- Enforced (#273): a districted seat of this office needs a qualifier
    -- (e.g. a per-position House seat). resolve_role REJECTS a jurisdictional
    -- observation of this office with a NULL qualifier — kills the #267
    -- positionless-seat mint. Unlike expects_jurisdiction, this IS enforced.
    requires_qualifier BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Core Entities
-- =============================================================================

CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT        PRIMARY KEY,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    parent_id   TEXT        REFERENCES organizations(id),
    CONSTRAINT chk_no_self_parent CHECK (id <> parent_id),
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

-- One row per name variant (legal, dba, former only); exactly one may have is_canonical = TRUE
CREATE TABLE IF NOT EXISTS organization_names (
    id              TEXT        PRIMARY KEY,
    organization_id TEXT        NOT NULL REFERENCES organizations(id),
    name            TEXT        NOT NULL,
    name_type       TEXT        NOT NULL DEFAULT 'legal'
                                CHECK (name_type IN ('legal', 'dba', 'former')),
    is_canonical    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One canonical name per org (regardless of type)
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_canonical_name
    ON organization_names(organization_id)
    WHERE is_canonical = TRUE;

-- Acronyms are a separate concept from names; one canonical acronym per org
CREATE TABLE IF NOT EXISTS organization_acronyms (
    id              TEXT        PRIMARY KEY,
    organization_id TEXT        NOT NULL REFERENCES organizations(id),
    acronym         TEXT        NOT NULL,
    is_canonical    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_org_canonical_acronym
    ON organization_acronyms(organization_id)
    WHERE is_canonical = TRUE;

-- Display name view: "Name (Acronym)" when both exist, else whichever is present.
-- Clean two-table join; no LATERAL, no name_type filters.
-- Used by all admin queries that show an org name for display (not editing).
CREATE OR REPLACE VIEW v_org_display_names AS
SELECT o.id AS organization_id,
       COALESCE(n.name || ' (' || a.acronym || ')', n.name, a.acronym) AS display_name
FROM organizations o
LEFT JOIN organization_names n
    ON n.organization_id = o.id AND n.is_canonical = TRUE
LEFT JOIN organization_acronyms a
    ON a.organization_id = o.id AND a.is_canonical = TRUE
;

CREATE TABLE IF NOT EXISTS people (
    id                TEXT        PRIMARY KEY,
    personal_pronouns TEXT,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS person_names (
    id           TEXT        PRIMARY KEY,
    person_id    TEXT        NOT NULL REFERENCES people(id),
    name         TEXT        NOT NULL,
    name_type    TEXT        NOT NULL DEFAULT 'legal'
                             CHECK (name_type IN (
                                 'legal','preferred','alias','former','initials',
                                 'maiden','religious','stage',
                                 'deadname',
                                 'reading','romanization','mrz',
                                 -- Issue #135: alt-spelling / nickname variant
                                 -- of an existing name on the same person.
                                 'variant'
                             )),
    is_canonical BOOLEAN     NOT NULL DEFAULT FALSE,

    -- i18n / cultural-awareness metadata (issue #121, Phase 1)
    locale              TEXT,                       -- BCP 47, e.g. 'en-US','zh-Hant-TW'
    script              TEXT,                       -- ISO 15924, e.g. 'Latn','Hant','Hans','Kana'
    sort_as             TEXT,                       -- explicit collation key; NULL → use `name`
    visibility          TEXT NOT NULL DEFAULT 'public'
                        CHECK (visibility IN ('public','legal_only','hidden')),
    -- Name-family edge (#121). Points a `reading` (furigana) or `romanization`
    -- (pinyin, romaji) row at the name row it renders. This is how PM models
    -- "these two rows are the same name written differently" — an explicit FK
    -- between rows, NOT an implied grouping of rows that happen to share
    -- (name_type, locale, script).
    --
    -- Load-bearing for #308: because families are edges, narrowing
    -- uq_person_canonical_name to (person_id) costs nothing. A Japanese legal
    -- name and its romaji rendering are two rows joined by reading_of_id, of
    -- two different name_types, of which exactly one is the display pointer —
    -- which is the correct answer and was never expressible by "one canonical
    -- per (name_type, locale, script)".
    --
    -- ON DELETE CASCADE: a reading cannot outlive the name it reads.
    reading_of_id       TEXT REFERENCES person_names(id) ON DELETE CASCADE,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Bootstrap form of the canonical-name index. The migration section drops this
-- and recreates it keyed on (person_id) alone — the display-pointer shape
-- (#308). Kept here so fresh DBs and pre-#121 DBs both parse this file
-- successfully; the migration block converges either starting state in a single
-- apply_schema run, since this two-column form also fails its "already
-- re-keyed?" guard.
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type)
    WHERE is_canonical = TRUE;

-- Structured name parts, sidecar to person_names (issue #121).
-- 1:0..1 with person_names — each name row optionally has its own decomposition.
-- Multiple decompositions per person are intentional: a Hant `legal` row and a
-- Latn `romanization` row each carry their own parts, not a shared set.
-- Never auto-parsed; populated only when an upstream source provides structure.
CREATE TABLE IF NOT EXISTS person_name_parts (
    person_name_id      TEXT        PRIMARY KEY
                                    REFERENCES person_names(id) ON DELETE CASCADE,
    given_names         TEXT[],
    family_names        TEXT[],
    additional_names    TEXT[],
    honorific_prefix    TEXT,
    honorific_suffix    TEXT,
    primary_identifier  TEXT
                        CHECK (primary_identifier IS NULL
                               OR primary_identifier IN ('family','given','patronymic','mononym')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Display name view: canonical name for a person.
-- Used by all admin queries that show a person name for display (not editing).
-- Bootstrap form (no visibility filter, sort_key = name). The migration
-- section CREATE OR REPLACEs this with the visibility-aware + sort_as-aware
-- form after the per-column ADD COLUMN blocks add `visibility` and
-- `sort_as`. Column count + types must match the post-migration form so
-- CREATE OR REPLACE VIEW succeeds on already-migrated DBs.
CREATE OR REPLACE VIEW v_person_display_names AS
SELECT p.id AS person_id,
       n.name AS display_name,
       n.name AS sort_key
FROM people p
LEFT JOIN person_names n
    ON n.person_id = p.id
   AND n.is_canonical = TRUE
;

-- Role = position definition at an organization (independent of who holds it or when)
CREATE TABLE IF NOT EXISTS roles (
    id              TEXT        PRIMARY KEY,
    organization_id TEXT        NOT NULL REFERENCES organizations(id),
    title           TEXT        NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at     TIMESTAMPTZ
);

-- Role assignment = person occupying a role during a time window
CREATE TABLE IF NOT EXISTS role_assignments (
    id         TEXT        PRIMARY KEY,
    person_id  TEXT        NOT NULL REFERENCES people(id),
    role_id    TEXT        NOT NULL REFERENCES roles(id),

    is_current BOOLEAN     NOT NULL DEFAULT FALSE,
    start_date DATE,                             -- nullable: unknown start is valid
    end_date   DATE,                             -- nullable: NULL + is_current = ongoing

    -- Cannot be current AND have an end date
    CONSTRAINT chk_current_no_end_date CHECK (NOT is_current OR end_date IS NULL),

    -- Email, phone → contact_methods; profile URL → urls (url_type = 'profile')

    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

-- Prevent duplicate role definitions per org (case-insensitive title).
-- Archived roles are excluded so a re-created role is never blocked.
-- NOTE: index creation is skipped (with a warning) if duplicate rows exist.
-- Run scripts/deduplicate_roles.py --execute before applying this schema if the
-- index fails to create, then re-run apply_schema to pick it up.
--
-- SUPERSEDED by the #261 role-uniqueness split further below (search
-- "Role structural fields"): that migration DROPs this pre-#261 form and
-- recreates uq_role_org_title with an added `jurisdiction_id IS NULL` predicate
-- (plus the structural index uq_role_structural). This block still creates the old form on
-- a fresh DB; the migration replaces it in the same apply. Kept here because the
-- predicate below can't reference jurisdiction_id, which doesn't exist yet at
-- this point in the file.
DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_role_org_title
        ON roles (organization_id, lower(title))
        WHERE archived_at IS NULL;
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING
        'uq_role_org_title not created: duplicate (organization_id, title) rows exist. '
        'Run scripts/deduplicate_roles.py --execute then re-apply schema.';
END $$;

-- Prevent duplicate assignments: same person+role+start_date is always a duplicate.
-- NULLS NOT DISTINCT treats NULL start_date as a known value (unknown-start is unique).
-- Archived assignments are excluded so re-creating an archived record is allowed.
-- Replaces uq_role_assignment_current (strictly stronger).
DROP INDEX IF EXISTS uq_role_assignment_current;

DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_role_assignment_person_role_start
        ON role_assignments (person_id, role_id, start_date) NULLS NOT DISTINCT
        WHERE archived_at IS NULL;
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING
        'uq_role_assignment_person_role_start not created: duplicate (person_id, role_id, '
        'start_date) rows exist. Run scripts/deduplicate_roles.py --execute then re-apply schema.';
END $$;

-- =============================================================================
-- Jurisdiction Entities (#168)
-- =============================================================================

CREATE TABLE IF NOT EXISTS jurisdictions (
    id            TEXT        PRIMARY KEY,
    slug          TEXT        NOT NULL UNIQUE,
    name          TEXT        NOT NULL,
    type_id       TEXT        NOT NULL REFERENCES jurisdiction_types(id),
    valid_from    DATE,
    valid_until   DATE,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at TIMESTAMPTZ,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at   TIMESTAMPTZ,
    CONSTRAINT chk_jurisdiction_valid_range
        CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until)
);

CREATE INDEX IF NOT EXISTS idx_jurisdictions_type_id
    ON jurisdictions(type_id);

-- Typed, bitemporal edges in the jurisdiction graph.
-- For symmetric rel_types (is_symmetric=TRUE on jurisdiction_relationship_types),
-- query both directions: WHERE (from_id = $id OR to_id = $id).
CREATE TABLE IF NOT EXISTS jurisdiction_relationships (
    id            TEXT        PRIMARY KEY,
    from_id       TEXT        NOT NULL REFERENCES jurisdictions(id),
    to_id         TEXT        NOT NULL REFERENCES jurisdictions(id),
    rel_type_id   TEXT        NOT NULL REFERENCES jurisdiction_relationship_types(id),
    valid_from    DATE,
    valid_until   DATE,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at TIMESTAMPTZ,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_no_self_rel CHECK (from_id <> to_id),
    CONSTRAINT chk_rel_valid_range
        CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until)
);

CREATE INDEX IF NOT EXISTS idx_jurisdiction_rels_from
    ON jurisdiction_relationships(from_id);
CREATE INDEX IF NOT EXISTS idx_jurisdiction_rels_to
    ON jurisdiction_relationships(to_id);
CREATE INDEX IF NOT EXISTS idx_jurisdiction_rels_type
    ON jurisdiction_relationships(rel_type_id);

CREATE OR REPLACE VIEW v_jurisdiction_display_names AS
SELECT j.id   AS jurisdiction_id,
       j.name AS display_name,
       j.slug
FROM jurisdictions j;

-- Placed here (after jurisdictions) because of the FK to jurisdictions(id).
CREATE TABLE IF NOT EXISTS organization_jurisdiction_affiliations (
    id                   TEXT        PRIMARY KEY,
    organization_id      TEXT        NOT NULL REFERENCES organizations(id),
    jurisdiction_id      TEXT        NOT NULL REFERENCES jurisdictions(id),
    affiliation_type_id  TEXT        NOT NULL
                                     REFERENCES organization_jurisdiction_affiliation_types(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_org_jur_affiliation
    ON organization_jurisdiction_affiliations(organization_id, jurisdiction_id, affiliation_type_id);

CREATE INDEX IF NOT EXISTS idx_org_jur_affiliations_org
    ON organization_jurisdiction_affiliations(organization_id);

CREATE INDEX IF NOT EXISTS idx_org_jur_affiliations_jur
    ON organization_jurisdiction_affiliations(jurisdiction_id);

-- =============================================================================
-- Polymorphic Tables
-- =============================================================================

-- Phone numbers and email addresses for any entity
CREATE TABLE IF NOT EXISTS contact_methods (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL
                              CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment', 'jurisdiction')),
    entity_id     TEXT        NOT NULL,
    contact_type  TEXT        NOT NULL CHECK (contact_type IN ('email', 'phone')),
    value         TEXT        NOT NULL,          -- E.164 for phone; validated addr for email
    display_label TEXT,                          -- 'Work', 'Mobile', 'Direct', …
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contact_methods_entity
    ON contact_methods(entity_type, entity_id);

-- Web URLs and social links for any entity; link_type_id references link_types
CREATE TABLE IF NOT EXISTS links (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL
                              CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment', 'jurisdiction')),
    entity_id     TEXT        NOT NULL,
    url           TEXT        NOT NULL,
    link_type_id  TEXT        NOT NULL REFERENCES link_types(id),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_links_entity
    ON links(entity_type, entity_id);

-- Natural-key uniqueness (issue #142): an entity must not carry the same URL
-- twice for the same link_type. Without this, the ON CONFLICT DO NOTHING
-- clauses in src/core/ingestion/pipeline.py are silent no-ops, and the
-- conflict key relied on by scripts/deduplicate_roles.py is unenforced.
-- is_active is intentionally excluded — keeping both an active and an
-- archived copy of the same URL is not a supported state.
--
-- Self-healing migration: collapse any pre-existing duplicate rows (active
-- wins ties, then oldest by created_at/id) before creating the index. The
-- DELETE is gated on the index not yet existing so it only runs once per
-- DB; matches the project's other migration-block style (see urls→links
-- migration below). A standalone dry-run / audit entry point lives at
-- scripts/dedup_links.py.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = 'uq_links_entity_url'
    ) THEN
        DELETE FROM links
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY entity_type, entity_id, url, link_type_id
                    ORDER BY is_active DESC, created_at, id
                ) AS rn
                FROM links
            ) ranked
            WHERE ranked.rn > 1
        );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_links_entity_url
    ON links(entity_type, entity_id, url, link_type_id);

-- Remove is_canonical column and index (no display query uses them; concept retired).
-- Application-level callers removed in c71270a (orgs_merge.py pre-demote block).
DROP INDEX IF EXISTS uq_link_canonical;
ALTER TABLE links DROP COLUMN IF EXISTS is_canonical;

-- entity_type is encoded in entity_identifier_types; no need to duplicate here
CREATE TABLE IF NOT EXISTS identifiers (
    id                        TEXT        PRIMARY KEY,
    entity_id                 TEXT        NOT NULL,
    entity_identifier_type_id TEXT        NOT NULL REFERENCES entity_identifier_types(id),
    value                     TEXT        NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_identifiers_entity
    ON identifiers(entity_identifier_type_id, entity_id);

CREATE INDEX IF NOT EXISTS idx_identifiers_lookup
    ON identifiers(entity_identifier_type_id, value);

-- =============================================================================
-- Schema evolution: archived_at columns
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='organizations' AND column_name='archived_at'
    ) THEN
        ALTER TABLE organizations ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='people' AND column_name='archived_at'
    ) THEN
        ALTER TABLE people ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='archived_at'
    ) THEN
        ALTER TABLE roles ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='roles' AND constraint_name='chk_roles_title_nonempty'
    ) THEN
        ALTER TABLE roles ADD CONSTRAINT chk_roles_title_nonempty CHECK (trim(title) <> '');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='role_assignments' AND column_name='archived_at'
    ) THEN
        ALTER TABLE role_assignments ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

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

-- =============================================================================
-- Role structural fields (#261): role_type_id + jurisdiction_id + qualifier make
-- a role a durable role tied to a district (role type + district + position).
-- Occupant/tenure stays in role_assignments. See
-- docs/plans/2026-07-03-legislator-*-design.md.
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='role_type_id'
    ) THEN
        ALTER TABLE roles ADD COLUMN role_type_id TEXT REFERENCES role_types(id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='jurisdiction_id'
    ) THEN
        ALTER TABLE roles ADD COLUMN jurisdiction_id TEXT REFERENCES jurisdictions(id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='qualifier'
    ) THEN
        ALTER TABLE roles ADD COLUMN qualifier TEXT;
    END IF;
END $$;

-- A role with a jurisdiction must name its role type. Rename the legacy
-- constraint if present (#271), else create it fresh.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='roles' AND constraint_name='chk_role_districted_needs_type'
    ) THEN
        ALTER TABLE roles RENAME CONSTRAINT chk_role_districted_needs_type
            TO chk_role_jurisdiction_needs_role_type;
    END IF;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='roles' AND constraint_name='chk_role_jurisdiction_needs_role_type'
    ) THEN
        ALTER TABLE roles ADD CONSTRAINT chk_role_jurisdiction_needs_role_type
            CHECK (jurisdiction_id IS NULL OR role_type_id IS NOT NULL);
    END IF;
END $$;

-- A qualifier only disambiguates roles with a jurisdiction — it must not appear
-- on a role without one (backstops any insert path; resolve_role also drops it).
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='roles' AND constraint_name='chk_role_qualifier_needs_jurisdiction'
    ) THEN
        ALTER TABLE roles ADD CONSTRAINT chk_role_qualifier_needs_jurisdiction
            CHECK (qualifier IS NULL OR jurisdiction_id IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_roles_role_type ON roles(role_type_id);
CREATE INDEX IF NOT EXISTS idx_roles_jurisdiction ON roles(jurisdiction_id);

-- Split role uniqueness (supersedes the (organization_id, lower(title)) form
-- created above):
--   * roles with a jurisdiction: identity = chamber + role type + district + position;
--     NULLS NOT DISTINCT makes a NULL qualifier unique per district (one senator).
--   * roles without a jurisdiction (leadership, generic): keep title-based identity.
-- Drop the pre-#261 index only if present in its old (no jurisdiction predicate)
-- form, so re-applies don't churn.
DO $$
DECLARE d TEXT;
BEGIN
    SELECT indexdef INTO d FROM pg_indexes
    WHERE schemaname='public' AND indexname='uq_role_org_title';
    IF d IS NOT NULL AND position('jurisdiction_id' IN d) = 0 THEN
        DROP INDEX uq_role_org_title;
    END IF;
END $$;

-- Rename the legacy seat index if present (#271).
ALTER INDEX IF EXISTS uq_role_seat RENAME TO uq_role_structural;

DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_role_structural
        ON roles (organization_id, role_type_id, jurisdiction_id, qualifier)
        NULLS NOT DISTINCT
        WHERE jurisdiction_id IS NOT NULL AND archived_at IS NULL;
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING
        'uq_role_structural not created: duplicate (org, role_type, jurisdiction, '
        'qualifier) rows exist. Resolve duplicates then re-apply schema.';
END $$;

DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_role_org_title
        ON roles (organization_id, lower(title))
        WHERE jurisdiction_id IS NULL AND archived_at IS NULL;
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING
        'uq_role_org_title not created: duplicate (organization_id, title) rows '
        'exist. Run scripts/deduplicate_roles.py --execute then re-apply schema.';
END $$;

-- organization_names real-world effective-date timeline (#239). Lets PM answer
-- "which name was in effect when". NULL effective_start = unknown lower bound
-- (treat as -infinity); NULL effective_end = still in effect (treat as +infinity).
-- Orthogonal to is_canonical (display pointer) and name_type (kind of name).
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='organization_names' AND column_name='effective_start'
    ) THEN
        ALTER TABLE organization_names ADD COLUMN effective_start DATE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='organization_names' AND column_name='effective_end'
    ) THEN
        ALTER TABLE organization_names ADD COLUMN effective_end DATE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name='organization_names'
          AND constraint_name='chk_org_name_effective_date_order'
    ) THEN
        ALTER TABLE organization_names ADD CONSTRAINT chk_org_name_effective_date_order
            CHECK (effective_start IS NULL OR effective_end IS NULL
                   OR effective_start <= effective_end);
    END IF;
END $$;

-- =============================================================================
-- Schema evolution: person_names i18n columns (issue #121, Phase 1)
-- One DO block per column, matching the archived_at migration pattern.
-- All columns nullable except `visibility` (constant default 'public').
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='locale') THEN
        ALTER TABLE person_names ADD COLUMN locale TEXT;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='script') THEN
        ALTER TABLE person_names ADD COLUMN script TEXT;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='sort_as') THEN
        ALTER TABLE person_names ADD COLUMN sort_as TEXT;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='visibility') THEN
        ALTER TABLE person_names ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
            CHECK (visibility IN ('public','legal_only','hidden'));
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='reading_of_id') THEN
        ALTER TABLE person_names ADD COLUMN reading_of_id TEXT
            REFERENCES person_names(id) ON DELETE CASCADE;
    END IF;
END $$;

-- Tighten visibility CHECK: drop ALL CHECK constraints whose conkey list
-- includes the visibility column (handles auto-named, explicitly-named, and
-- any vestigial duplicates), then add a single canonical one. Filters via
-- conkey @> ARRAY[<attnum>] — strict referential check, not text matching.
-- Idempotent — drops/re-adds on every apply_schema().
DO $$
DECLARE
    visibility_attnum SMALLINT;
    constraint_rec    RECORD;
BEGIN
    SELECT attnum INTO visibility_attnum
    FROM pg_attribute
    WHERE attrelid = 'person_names'::regclass
      AND attname = 'visibility'
      AND NOT attisdropped;

    IF visibility_attnum IS NULL THEN
        -- visibility column not yet added (per-column DO block runs earlier;
        -- this should never happen on a complete apply_schema run).
        RETURN;
    END IF;

    FOR constraint_rec IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'person_names'
          AND c.contype = 'c'
          AND c.conkey @> ARRAY[visibility_attnum]
    LOOP
        EXECUTE format('ALTER TABLE person_names DROP CONSTRAINT %I',
                       constraint_rec.conname);
    END LOOP;
    ALTER TABLE person_names ADD CONSTRAINT person_names_visibility_check
        CHECK (visibility IN ('public','legal_only','hidden'));
END $$;

-- Tighten reading_of_id FK to ON DELETE CASCADE if the constraint was created
-- with the default NO ACTION semantics by a prior draft.
DO $$
DECLARE
    fk_name TEXT;
BEGIN
    SELECT c.conname INTO fk_name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE t.relname  = 'person_names'
      AND c.contype  = 'f'
      AND c.confdeltype = 'a'  -- 'a' = NO ACTION
      AND c.conname  LIKE '%reading_of_id%'
    LIMIT 1;

    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE person_names DROP CONSTRAINT %I', fk_name);
        ALTER TABLE person_names
            ADD CONSTRAINT person_names_reading_of_id_fkey
            FOREIGN KEY (reading_of_id) REFERENCES person_names(id) ON DELETE CASCADE;
    END IF;
END $$;

-- Expand person_names.name_type to include i18n / cultural-awareness values.
-- Drop + add: PostgreSQL's auto-generated inline CHECK is named
-- 'person_names_name_type_check'. Existing rows are pre-validated to be in the
-- new set (see issue #121 pre-flight).
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.check_constraints
               WHERE constraint_name='person_names_name_type_check') THEN
        ALTER TABLE person_names DROP CONSTRAINT person_names_name_type_check;
    END IF;
    ALTER TABLE person_names ADD CONSTRAINT person_names_name_type_check CHECK (
        name_type IN (
            'legal','preferred','alias','former','initials',
            'maiden','religious','stage',
            'deadname',
            'reading','romanization','mrz',
            -- Issue #135: alt-spelling / nickname variant of an existing
            -- name on the same person. Sits next to its legal row.
            'variant'
        )
    );
END $$;

-- Re-key uq_person_canonical_name on (person_id) alone (issue #308).
--
-- `is_canonical` is the **display pointer**: the one name PM shows for a person.
-- Keying it per (name_type, locale, script) — the #121 shape — let one person
-- hold several canonical rows at once, so `v_person_display_names` had to pick
-- among them, and a canonical row in one slot could block promotion in another.
-- Nothing consumed the per-family meaning: every read in the codebase is either
-- `ORDER BY is_canonical DESC` (show the main name first) or a wholesale demote
-- on merge, and the admin promote path has always demoted person-wide.
--
-- This mirrors uq_org_canonical_name, which was narrowed from
-- (organization_id, name_type) to (organization_id) for exactly this reason.
--
-- Name *families* are unaffected: a reading/romanization is linked to its source
-- row by the reading_of_id FK, not by sharing a canonical slot. See the
-- reading_of_id column comment on person_names.
--
-- Surplus canonicals are demoted deterministically before the index is built:
-- keep a public row over a non-public one, then the best display name_type, then
-- the lowest id. Production held zero such rows at 2026-07-19; this is a
-- safety net for dev/test databases.
DO $$
DECLARE
    demoted INTEGER;
BEGIN
    IF EXISTS (
        -- Scoped to the index on person_names in the current search_path, not
        -- to the bare name: pg_indexes spans every schema (CR5 #50).
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'uq_person_canonical_name'
          AND schemaname = current_schema()
          AND tablename = 'person_names'
          AND indexdef NOT LIKE '%(person_id)%WHERE%'
    ) THEN
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY person_id
                       ORDER BY (visibility = 'public') DESC,
                                CASE name_type
                                    WHEN 'preferred' THEN 1 WHEN 'legal'    THEN 2
                                    WHEN 'alias'     THEN 3 WHEN 'stage'    THEN 4
                                    WHEN 'religious' THEN 5 WHEN 'maiden'   THEN 6
                                    WHEN 'variant'   THEN 7 WHEN 'former'   THEN 8
                                    WHEN 'initials'  THEN 9 ELSE 99
                                END,
                                id
                   ) AS rn
            FROM person_names
            WHERE is_canonical = TRUE
        )
        UPDATE person_names SET is_canonical = FALSE
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        GET DIAGNOSTICS demoted = ROW_COUNT;
        IF demoted > 0 THEN
            RAISE NOTICE 'uq_person_canonical_name re-key: demoted % surplus canonical person_names row(s)', demoted;
        END IF;
        DROP INDEX uq_person_canonical_name;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id)
    WHERE is_canonical = TRUE;

-- The display pointer must be displayable (issue #308).
--
-- v_person_display_names filters to visibility='public', so a canonical row that
-- is legal_only or hidden renders the person blank *and* occupies the only
-- canonical slot — unreachable state, silently. The CHECK makes it an error at
-- write time instead. It also closes the deadname path structurally:
-- trg_deadname_visibility rewrites a deadname row to legal_only BEFORE INSERT,
-- so `is_canonical = TRUE` on a deadname now fails loudly rather than sealing
-- the person's display slot with an invisible row.
DO $$
DECLARE
    demoted INTEGER;
BEGIN
    IF NOT EXISTS (
        -- conrelid, not conname alone: constraint names are unique per table,
        -- not per database, so a same-named constraint elsewhere would make
        -- this block skip and leave person_names silently unconstrained.
        -- The regclass cast resolves via search_path, so this is schema-correct
        -- as well as table-correct (CR5 #50).
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_person_canonical_is_public'
          AND conrelid = 'person_names'::regclass
    ) THEN
        UPDATE person_names SET is_canonical = FALSE
        WHERE is_canonical = TRUE AND visibility <> 'public';
        GET DIAGNOSTICS demoted = ROW_COUNT;
        IF demoted > 0 THEN
            RAISE NOTICE 'chk_person_canonical_is_public: demoted % non-public canonical row(s)', demoted;
        END IF;
        ALTER TABLE person_names ADD CONSTRAINT chk_person_canonical_is_public
            CHECK (NOT is_canonical OR visibility = 'public');
    END IF;
END $$;

-- Recreate v_person_display_names with the visibility-aware filter, now that
-- the per-column DO blocks above have added `visibility` to person_names.
-- sort_key (Phase 2b, #123) is the value to ORDER BY when sorting people:
-- COALESCE(sort_as, name). Combine with `COLLATE "und-x-icu"` at query
-- time so locale-aware diacritic ordering applies (ICU "und" puts Å near A,
-- not after Z as ASCII does).
--
-- One row per person by construction (issue #308): uq_person_canonical_name is
-- keyed on (person_id) alone and chk_person_canonical_is_public guarantees the
-- canonical row is visible, so there is nothing to disambiguate. The earlier
-- form needed DISTINCT ON plus a name_type priority ladder to pick among the
-- several canonical rows the per-family key allowed; both are gone.
--
-- The visibility filter is retained as belt-and-braces. It is redundant against
-- the CHECK, but keeps the view correct if that constraint is ever relaxed.
CREATE OR REPLACE VIEW v_person_display_names AS
SELECT p.id AS person_id,
       n.name AS display_name,
       COALESCE(n.sort_as, n.name) AS sort_key
FROM people p
LEFT JOIN person_names n
    ON n.person_id = p.id
   AND n.is_canonical = TRUE
   AND n.visibility = 'public'
;

-- Phase 2-prep (#123): bind person_names.locale → bcp47_locales(code)
-- and person_names.script → iso15924_scripts(code). Idempotent — only
-- adds the constraint when absent. ON UPDATE CASCADE so registry-driven
-- code renames propagate to existing person_names rows.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'person_names'
          AND c.conname = 'person_names_locale_fkey'
    ) THEN
        ALTER TABLE person_names
            ADD CONSTRAINT person_names_locale_fkey
            FOREIGN KEY (locale) REFERENCES bcp47_locales(code) ON UPDATE CASCADE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'person_names'
          AND c.conname = 'person_names_script_fkey'
    ) THEN
        ALTER TABLE person_names
            ADD CONSTRAINT person_names_script_fkey
            FOREIGN KEY (script) REFERENCES iso15924_scripts(code) ON UPDATE CASCADE;
    END IF;
END $$;

-- Symmetric FK: bcp47_locales.script → iso15924_scripts(code). Without
-- this, langcodes-driven seeding could store a script tag absent from
-- pycountry's ISO 15924 list (registry skew), breaking any future join
-- that enriches locale rows with their script's display name.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'bcp47_locales'
          AND c.conname = 'bcp47_locales_script_fkey'
    ) THEN
        ALTER TABLE bcp47_locales
            ADD CONSTRAINT bcp47_locales_script_fkey
            FOREIGN KEY (script) REFERENCES iso15924_scripts(code) ON UPDATE CASCADE;
    END IF;
END $$;

-- =============================================================================
-- Organization names/acronyms schema migration
-- Moves acronym rows from organization_names to organization_acronyms,
-- updates the CHECK constraint, and replaces the per-(org, name_type) unique
-- index with a per-org index. Idempotent: safe to re-run.
-- =============================================================================

-- Step 1: Migrate existing acronym rows to organization_acronyms (idempotent)
DO $$ BEGIN
    INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical, created_at)
    SELECT id, organization_id, name, is_canonical, created_at
    FROM organization_names
    WHERE name_type = 'acronym'
    ON CONFLICT (id) DO NOTHING;

    DELETE FROM organization_names WHERE name_type = 'acronym';
END $$;

-- Step 2: Update CHECK constraint to exclude 'acronym' (only if still on old definition)
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'organization_names_name_type_check'
          AND check_clause LIKE '%acronym%'
    ) THEN
        ALTER TABLE organization_names
            DROP CONSTRAINT organization_names_name_type_check;
        ALTER TABLE organization_names
            ADD CONSTRAINT organization_names_name_type_check
            CHECK (name_type IN ('legal', 'dba', 'former'));
    END IF;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

-- Step 3: Replace per-(org, name_type) index with per-org index.
-- Drop old index (if still the old per-(org, name_type) form); the IF NOT EXISTS
-- on the CREATE above handles the fresh-DB case; here we handle existing DBs.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'uq_org_canonical_name'
          AND indexdef LIKE '%organization_id, name_type%'
    ) THEN
        DROP INDEX uq_org_canonical_name;
        CREATE UNIQUE INDEX uq_org_canonical_name
            ON organization_names(organization_id)
            WHERE is_canonical = TRUE;
    END IF;
END $$;

-- =============================================================================
-- Organization Hierarchy Cycle Prevention
-- Fires BEFORE INSERT OR UPDATE; raises if setting parent_id would create
-- an ancestor cycle (A → B → A, or longer chains).
-- =============================================================================

CREATE OR REPLACE FUNCTION chk_no_org_cycle()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id
              FROM organizations
             WHERE id = NEW.parent_id
            UNION ALL
            SELECT o.id, o.parent_id
              FROM organizations o
              JOIN ancestors a ON o.id = a.parent_id
        )
        SELECT 1 FROM ancestors WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION
            'org hierarchy cycle detected: % would become an ancestor of itself',
            NEW.id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_no_org_cycle
    BEFORE INSERT OR UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION chk_no_org_cycle();

-- =============================================================================
-- Deadname → visibility consistency (issue #121)
-- A 'deadname' row can never be 'public'; coerce to 'legal_only' if so.
-- Explicit 'hidden' is preserved (more restrictive, intentional).
-- =============================================================================

CREATE OR REPLACE FUNCTION enforce_deadname_visibility()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.name_type = 'deadname' AND NEW.visibility = 'public' THEN
        NEW.visibility := 'legal_only';
    END IF;
    RETURN NEW;
END;
$$;

-- Surgical: only fires when name_type or visibility actually changes (or on INSERT).
CREATE OR REPLACE TRIGGER trg_deadname_visibility
    BEFORE INSERT OR UPDATE OF name_type, visibility ON person_names
    FOR EACH ROW EXECUTE FUNCTION enforce_deadname_visibility();

-- =============================================================================
-- updated_at Trigger
-- Automatically sets updated_at = NOW() on every UPDATE, for all tables
-- that carry an updated_at column.
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_updated_at_addresses
    BEFORE UPDATE ON addresses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_organizations
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_people
    BEFORE UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_roles
    BEFORE UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_role_assignments
    BEFORE UPDATE ON role_assignments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_person_name_parts
    BEFORE UPDATE ON person_name_parts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_bcp47_locales
    BEFORE UPDATE ON bcp47_locales
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_iso15924_scripts
    BEFORE UPDATE ON iso15924_scripts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_updated_at_jurisdictions
    BEFORE UPDATE ON jurisdictions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- Touch-parent triggers
-- Propagate child INSERT/UPDATE/DELETE to the parent entity's updated_at so
-- that ETag-based conditional GETs reflect any change to the full detail payload.
-- =============================================================================

CREATE OR REPLACE FUNCTION touch_parent_org()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE organizations SET updated_at = NOW()
    WHERE id = COALESCE(NEW.organization_id, OLD.organization_id);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_org_on_name_change
    AFTER INSERT OR UPDATE OR DELETE ON organization_names
    FOR EACH ROW EXECUTE FUNCTION touch_parent_org();

CREATE OR REPLACE TRIGGER trg_touch_org_on_acronym_change
    AFTER INSERT OR UPDATE OR DELETE ON organization_acronyms
    FOR EACH ROW EXECUTE FUNCTION touch_parent_org();

CREATE OR REPLACE TRIGGER trg_touch_org_on_affiliation_change
    AFTER INSERT OR UPDATE OR DELETE ON organization_jurisdiction_affiliations
    FOR EACH ROW EXECUTE FUNCTION touch_parent_org();

-- Affiliation changes also touch the jurisdiction side so a jurisdiction
-- subscriber sees org-affiliation edits (symmetry with the relationship-edge
-- touch below; #275 Phase 3).
CREATE OR REPLACE FUNCTION touch_jurisdiction_on_affiliation_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE jurisdictions SET updated_at = NOW()
    WHERE id = COALESCE(NEW.jurisdiction_id, OLD.jurisdiction_id);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_jurisdiction_on_affiliation_change
    AFTER INSERT OR UPDATE OR DELETE ON organization_jurisdiction_affiliations
    FOR EACH ROW EXECUTE FUNCTION touch_jurisdiction_on_affiliation_change();

-- Touch both endpoints of a relationship edge so a graph edit surfaces on the
-- jurisdiction change feed (mirrors touch_parent_org for org affiliations; #275
-- Phase 3). The UPDATE fires trg_entity_changes_jurisdictions on each endpoint.
CREATE OR REPLACE FUNCTION touch_parent_jurisdiction()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE jurisdictions SET updated_at = NOW()
    WHERE id IN (COALESCE(NEW.from_id, OLD.from_id), COALESCE(NEW.to_id, OLD.to_id));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_jurisdiction_on_relationship_change
    AFTER INSERT OR UPDATE OR DELETE ON jurisdiction_relationships
    FOR EACH ROW EXECUTE FUNCTION touch_parent_jurisdiction();

CREATE OR REPLACE FUNCTION touch_parent_person()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE people SET updated_at = NOW()
    WHERE id = COALESCE(NEW.person_id, OLD.person_id);
    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_person_on_name_change
    AFTER INSERT OR UPDATE OR DELETE ON person_names
    FOR EACH ROW EXECUTE FUNCTION touch_parent_person();

-- identifiers is polymorphic: look up entity_type from entity_identifier_types
-- to dispatch the touch to the correct parent table.
CREATE OR REPLACE FUNCTION touch_parent_on_identifier_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_entity_id   TEXT;
    v_type_id     TEXT;
    v_entity_type TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_entity_id := OLD.entity_id;
        v_type_id   := OLD.entity_identifier_type_id;
    ELSE
        v_entity_id := NEW.entity_id;
        v_type_id   := NEW.entity_identifier_type_id;
    END IF;

    SELECT entity_type INTO v_entity_type
    FROM entity_identifier_types
    WHERE id = v_type_id;

    IF v_entity_type = 'organization' THEN
        UPDATE organizations SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'person' THEN
        UPDATE people SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'jurisdiction' THEN
        UPDATE jurisdictions SET updated_at = NOW() WHERE id = v_entity_id;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_entity_on_identifier_change
    AFTER INSERT OR UPDATE OR DELETE ON identifiers
    FOR EACH ROW EXECUTE FUNCTION touch_parent_on_identifier_change();

-- entity_addresses is polymorphic: dispatch the touch on entity_type (#181).
-- Cascades into the entity_changes outbox via each parent's
-- fn_record_entity_change AFTER UPDATE trigger.
CREATE OR REPLACE FUNCTION touch_parent_on_entity_address_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_entity_type TEXT;
    v_entity_id   TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_entity_type := OLD.entity_type;
        v_entity_id   := OLD.entity_id;
    ELSE
        v_entity_type := NEW.entity_type;
        v_entity_id   := NEW.entity_id;
    END IF;

    IF v_entity_type = 'organization' THEN
        UPDATE organizations SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'person' THEN
        UPDATE people SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'jurisdiction' THEN
        UPDATE jurisdictions SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'role' THEN
        UPDATE roles SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'role_assignment' THEN
        UPDATE role_assignments SET updated_at = NOW() WHERE id = v_entity_id;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_entity_on_address_change
    AFTER INSERT OR UPDATE OR DELETE ON entity_addresses
    FOR EACH ROW EXECUTE FUNCTION touch_parent_on_entity_address_change();

-- =============================================================================
-- Migration: urls/social_links/url_types/platforms → link_types/links
-- Idempotent: checks table existence before operating. Safe to re-run.
-- =============================================================================
DO $$
BEGIN
    -- Migrate url_types → link_types (is_social = FALSE)
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'url_types' AND table_schema = 'public'
    ) THEN
        INSERT INTO link_types (id, slug, display_name, is_social)
        SELECT id, slug, display_name, FALSE FROM url_types
        ON CONFLICT (slug) DO NOTHING;
    END IF;

    -- Migrate platforms → link_types (is_social = TRUE)
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'platforms' AND table_schema = 'public'
    ) THEN
        INSERT INTO link_types (id, slug, display_name, is_social)
        SELECT id, slug, display_name, TRUE FROM platforms
        ON CONFLICT (slug) DO NOTHING;
    END IF;

    -- Migrate urls → links
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'urls' AND table_schema = 'public'
    ) THEN
        INSERT INTO links (id, entity_type, entity_id, url, link_type_id,
                           is_active, created_at)
        SELECT u.id, u.entity_type, u.entity_id, u.url,
               lt.id, TRUE, u.created_at
        FROM urls u
        JOIN url_types ut ON ut.id = u.url_type_id
        JOIN link_types lt ON lt.slug = ut.slug
        ON CONFLICT (id) DO NOTHING;

        DROP TABLE urls;
    END IF;

    -- Migrate social_links → links
    IF EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'social_links' AND table_schema = 'public'
    ) THEN
        INSERT INTO links (id, entity_type, entity_id, url, link_type_id,
                           is_active, created_at)
        SELECT sl.id, sl.entity_type, sl.entity_id, sl.url,
               lt.id, TRUE, sl.created_at
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

-- =============================================================================
-- Seed Data
-- =============================================================================

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
ON CONFLICT (id) DO UPDATE SET
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name,
    is_social    = EXCLUDED.is_social;

-- Extend entity_identifier_types.entity_type CHECK before seeding jurisdiction rows.
-- Must run before the INSERT below on existing DBs; CREATE TABLE IF NOT EXISTS
-- already carries the new shape for fresh DBs.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'entity_identifier_types_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE entity_identifier_types DROP CONSTRAINT entity_identifier_types_entity_type_check;
        ALTER TABLE entity_identifier_types ADD CONSTRAINT entity_identifier_types_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction'));
    END IF;
END $$;

-- Add is_internal column to existing DBs (#198).
-- Fresh DBs already have it from the CREATE TABLE above.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'entity_identifier_types'
          AND column_name  = 'is_internal'
    ) THEN
        ALTER TABLE entity_identifier_types
            ADD COLUMN is_internal BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;

INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name, is_internal) VALUES
    ('01KKZ3WGJSZF0F96SMYC000AVP', 'organization',    'org_ubi',       'UBI',    'Washington Unified Business Identifier',                   FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVQ', 'organization',    'org_wslcb',     'WSLCB',  'WA State Liquor and Cannabis Board License',               FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVR', 'organization',    'org_wa_pdc',    'WA PDC', 'Washington State Public Disclosure Commission',            FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVS', 'person',          'person_wa_pdc', 'WA PDC', 'Washington State Public Disclosure Commission',            FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVT', 'person',          'person_ssn',    'SSN',    'United States Social Security Number',                     FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVV', 'role_assignment', 'role_wa_pdc',   'WA PDC', 'Washington State Public Disclosure Commission',            FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVW', 'person',          'person_wa_legislature_member_id',  'WA Legislature', 'Washington State Legislature Member ID',        FALSE),
    ('01KKZ3WGJSZF0F96SMYC000AVX', 'organization',    'org_wa_legislature_committee_id',  'WA Legislature', 'Washington State Legislature Committee ID',     FALSE),
    -- Jurisdiction identifiers (#168)
    ('01KT0HK3452TNDD2WM8E50ZTBM', 'jurisdiction',    'jur_ocd',       'OCD',        'Open Civic Data Identifier',       FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBN', 'jurisdiction',    'jur_fips',      'FIPS',       'Census FIPS Code',                 FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBP', 'jurisdiction',    'jur_iso3166_2', 'ISO 3166-2', 'ISO 3166-2 Subdivision Code',      FALSE),
    -- Jurisdiction slug identifier (#183)
    ('01KT0HK3452TNDD2WM8E50ZTBQ', 'jurisdiction',    'jur_slug',      'Slug',       'Jurisdiction Slug',                FALSE),
    -- WA legislature org identifier types (#225)
    ('01KVJWW7YCKYMY4ZEEZWPKZT5H', 'organization',    'org_wa_legislature_chamber', 'WA Chamber',     'Washington State Legislature Chamber', FALSE),
    ('01KVJWW7YCKYMY4ZEEZWPKZT5J', 'organization',    'org_wa_legislature',         'WA Legislature', 'Washington State Legislature',        FALSE),
    -- WA political-party org identifier (#270): deterministic attach of a party
    -- Org by stable key (value convention: bare lowercase slug — democratic,
    -- republican). Existing party Orgs backfilled via a one-off script.
    ('01KWW0ZAM6C540AD40V48QBYJM', 'organization',    'org_wa_party',               'WA Party',       'Washington State Political Party',     FALSE),
    -- PM-native internal identifiers (#198): resolve directly by PM ULID, never NEW
    ('01KTVHGATRG3WEN9NXATTA2RA9', 'organization',    'pm_org_id',        'PM Org',        'Power Map Organization ID',             TRUE),
    ('01KTVHGATRG3WEN9NXATTA2RAA', 'person',          'pm_person_id',     'PM Person',     'Power Map Person ID',                   TRUE),
    ('01KTVHGATRG3WEN9NXATTA2RAB', 'jurisdiction',    'pm_jur_id',        'PM Jur',        'Power Map Jurisdiction ID',             TRUE),
    ('01KTVHGATRG3WEN9NXATTA2RAC', 'role_assignment', 'pm_assignment_id', 'PM Assignment', 'Power Map Role Assignment ID',          TRUE),
    -- Observo manual speaker labeling (#279): operator-created Person from voice
    -- diarization. Value is an Observo-generated ULID (opaque, stable per manual
    -- create). is_internal=FALSE so observations can create a new Person; cross-
    -- real-person dedupe happens via voice /identify, not this identifier.
    ('01KWZ3FM3H7M35RFEJWVBRBNZ1', 'person',          'observo_speaker',  'Observo Speaker', 'Observo Manual Speaker Label',       FALSE),
    -- WA PDC filer registration key (#293): person_wa_pdc holds PDC's numeric
    -- person_id (person-stable key); the per-filer registration key extracted
    -- from the legacy URL-form values lives here. Backfilled by
    -- scripts/migrate_person_wa_pdc_identifiers.py.
    ('01KXK8AYH1MTCEZ9E5G2VR28KE', 'person',          'person_wa_pdc_filer', 'WA PDC Filer', 'Washington State Public Disclosure Commission Filer ID', FALSE),
    -- WA PDC lobbyist agent key (#295): person-stable agent_id from PDC's
    -- Lobbyist Agents SODA dataset (bp5b-jrti). A person may carry multiple
    -- values (PDC keeps duplicate agent rows for name variants). Replaces the
    -- legacy accesshub node URLs, which keyed the lobbyist FIRM's directory
    -- page, not the person. Backfilled by
    -- scripts/migrate_person_wa_pdc_accesshub.py.
    ('01KXKPV2N8WK22EAQ0Q1NZG7RJ', 'person',          'person_wa_pdc_lobbyist_agent', 'WA PDC Lobbyist Agent', 'Washington State Public Disclosure Commission Lobbyist Agent ID', FALSE),
    -- WA PDC campaign-finance committee key (#296): org_wa_pdc holds the numeric
    -- LOBBYIST firm filer_id; a PAC/committee's CAMPAIGN-FINANCE filer_id is a
    -- distinct PDC subsystem and lives here (value convention: the bare
    -- space-padded committee filer_id, e.g. 'LABORG 503', mirroring
    -- person_wa_pdc_filer). Backfilled from legacy campaign-explorer committee
    -- URLs by scripts/cleanup_org_wa_pdc_remnants.py.
    ('01KXKZY20D3FT8VN9310XKJQX0', 'organization',    'org_wa_pdc_committee', 'WA PDC Committee', 'Washington State Public Disclosure Commission Campaign-Finance Committee Filer ID', FALSE)
ON CONFLICT (id) DO UPDATE SET
    entity_type  = EXCLUDED.entity_type,
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name,
    full_name    = EXCLUDED.full_name,
    is_internal  = EXCLUDED.is_internal;

-- =============================================================================
-- Jurisdiction Seed Data (#168)
-- =============================================================================

INSERT INTO jurisdiction_types (id, slug, display_name) VALUES
    ('01KT0HK3452TNDD2WM8E50ZTAS', 'country',                    'Country'),
    ('01KT0HK3452TNDD2WM8E50ZTAT', 'state',                      'State'),
    ('01KT0HK3452TNDD2WM8E50ZTAV', 'county',                     'County'),
    ('01KT0HK3452TNDD2WM8E50ZTAW', 'city',                       'City'),
    ('01KT0HK3452TNDD2WM8E50ZTAX', 'legislative_district_upper', 'Legislative District (Upper)'),
    ('01KT0HK3452TNDD2WM8E50ZTAY', 'legislative_district_lower', 'Legislative District (Lower)'),
    ('01KTG6F35E4PW9PJXJ88MHY0QB', 'legislative_district',       'Legislative District'),
    ('01KT0HK3452TNDD2WM8E50ZTAZ', 'congressional_district',     'Congressional District'),
    ('01KT0HK3452TNDD2WM8E50ZTB0', 'tribal',                     'Tribal'),
    ('01KT0HK3452TNDD2WM8E50ZTB1', 'territory',                  'Territory'),
    ('01KT0HK3452TNDD2WM8E50ZTB2', 'special_district',           'Special District'),
    ('01KT0HK3452TNDD2WM8E50ZTB3', 'school_district',            'School District'),
    ('01KT0HK3452TNDD2WM8E50ZTB4', 'judicial_district',          'Judicial District'),
    ('01KT0HK3452TNDD2WM8E50ZTB5', 'metropolitan',               'Metropolitan'),
    ('01KT0HK3452TNDD2WM8E50ZTB6', 'borough',                    'Borough'),
    ('01KT0HK3452TNDD2WM8E50ZTB7', 'township',                   'Township'),
    ('01KT0HK3452TNDD2WM8E50ZTB8', 'village',                    'Village')
ON CONFLICT (id) DO UPDATE SET
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name;

INSERT INTO jurisdiction_relationship_types (id, slug, display_name, category, is_symmetric) VALUES
    ('01KT0HK3452TNDD2WM8E50ZTB9', 'is_fully_contained_by', 'Is Fully Contained By', 'spatial', FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBA', 'borders',          'Borders',           'spatial',    TRUE),
    ('01KT0HK3452TNDD2WM8E50ZTBB', 'overlaps',         'Overlaps',          'spatial',    TRUE),
    ('01KT0HK3452TNDD2WM8E50ZTBC', 'governs',          'Governs',           'governance', FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBD', 'delegates_to',     'Delegates To',      'governance', FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBE', 'administers',      'Administers',       'governance', FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBF', 'represents',       'Represents',        'functional', FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBG', 'coextensive_with', 'Coextensive With',  'functional', TRUE),
    ('01KT0HK3452TNDD2WM8E50ZTBH', 'supersedes',       'Supersedes',        'lineage',    FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBJ', 'evolved_from',     'Evolved From',      'lineage',    FALSE),
    ('01KT0HK3452TNDD2WM8E50ZTBK', 'merged_into',      'Merged Into',       'lineage',    FALSE)
ON CONFLICT (id) DO UPDATE SET
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name,
    category     = EXCLUDED.category,
    is_symmetric = EXCLUDED.is_symmetric;

-- Rename is_seat → expects_jurisdiction on existing DBs (#271); add it on DBs
-- predating both (#268). Fresh DBs already have expects_jurisdiction from the
-- CREATE TABLE above.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='role_types' AND column_name='is_seat'
    ) THEN
        ALTER TABLE role_types RENAME COLUMN is_seat TO expects_jurisdiction;
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='role_types'
          AND column_name='expects_jurisdiction'
    ) THEN
        ALTER TABLE role_types ADD COLUMN expects_jurisdiction BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;

-- Add requires_qualifier on existing DBs (#273). Fresh DBs already have it from
-- the CREATE TABLE above.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='role_types'
          AND column_name='requires_qualifier'
    ) THEN
        ALTER TABLE role_types ADD COLUMN requires_qualifier BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
END $$;

-- Role-type classifier seed (#261). Extend as new offices are modeled
-- (speaker, majority_leader, committee_chair, ...). expects_jurisdiction marks
-- an office normally attached with a jurisdiction (#268/#271) — the two office
-- rows are; the upsert backfills it on existing rows. `member` (#269) is the
-- coarse, jurisdiction-less membership classifier (committee membership, or a
-- chamber member whose precise seat isn't yet positionable) — expects_jurisdiction
-- is FALSE; it attaches by (org, title) like any plain role. requires_qualifier
-- (#273) is TRUE only for per-position offices: a WA House seat is positioned
-- (Position 1/2), a state senator is one-per-district (NULL qualifier is valid).
INSERT INTO role_types (id, slug, display_name, expects_jurisdiction, requires_qualifier) VALUES
    ('01KX0000000000000000000001', 'state_representative', 'State Representative', TRUE,  TRUE),
    ('01KX0000000000000000000002', 'state_senator',        'State Senator',        TRUE,  FALSE),
    ('01KX0000000000000000000003', 'member',               'Member',               FALSE, FALSE)
ON CONFLICT (id) DO UPDATE SET
    slug                 = EXCLUDED.slug,
    display_name         = EXCLUDED.display_name,
    expects_jurisdiction = EXCLUDED.expects_jurisdiction,
    requires_qualifier   = EXCLUDED.requires_qualifier;

-- requires_qualifier DB backstop (#273): resolve_role rejects a positionless
-- districted seat at the app layer, but the admin dashboard and direct INSERTs
-- bypass it — enforce the same rule here so no code path can mint one. Cannot be
-- a CHECK (it references another table); a BEFORE trigger is the tool. Defined
-- after the seed so role_types.requires_qualifier is guaranteed to exist.
CREATE OR REPLACE FUNCTION enforce_role_requires_qualifier()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jurisdiction_id IS NOT NULL
       AND NEW.qualifier IS NULL
       AND NEW.role_type_id IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM role_types
           WHERE id = NEW.role_type_id AND requires_qualifier
       ) THEN
        RAISE EXCEPTION
            'role_type % requires a qualifier for a districted seat', NEW.role_type_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- Only fires when the columns that decide the outcome change (or on INSERT).
CREATE OR REPLACE TRIGGER trg_role_requires_qualifier
    BEFORE INSERT OR UPDATE OF role_type_id, jurisdiction_id, qualifier ON roles
    FOR EACH ROW EXECUTE FUNCTION enforce_role_requires_qualifier();

-- =============================================================================
-- Entity Event Types Seed Data (#170)
-- =============================================================================

INSERT INTO entity_event_types (id, slug, display_name, applies_to, requires_year, requires_linked_entity) VALUES
    ('01KV0000000000000000000001', 'birth',           'Birth',        'person',       TRUE,  FALSE),
    ('01KV0000000000000000000002', 'death',           'Death',        'person',       TRUE,  FALSE),
    ('01KV0000000000000000000003', 'marriage',        'Marriage',     'person',       FALSE, TRUE),
    ('01KV0000000000000000000004', 'divorce',         'Divorce',      'person',       FALSE, TRUE),
    ('01KV0000000000000000000005', 'naturalization',  'Naturalization','person',      FALSE, FALSE),
    ('01KV0000000000000000000006', 'founded',         'Founded',      'organization', TRUE,  FALSE),
    ('01KV0000000000000000000007', 'dissolved',       'Dissolved',    'organization', TRUE,  FALSE),
    ('01KV0000000000000000000008', 'merged_with',     'Merged With',  'organization', FALSE, TRUE),
    ('01KV0000000000000000000009', 'split_from',      'Split From',   'organization', FALSE, TRUE),
    ('01KV000000000000000000000A', 'renamed',         'Renamed',      'organization', FALSE, FALSE),
    ('01KV000000000000000000000B', 'other',           'Other',        'both',         FALSE, FALSE)
ON CONFLICT (id) DO UPDATE SET
    slug                   = EXCLUDED.slug,
    display_name           = EXCLUDED.display_name,
    applies_to             = EXCLUDED.applies_to,
    requires_year          = EXCLUDED.requires_year,
    requires_linked_entity = EXCLUDED.requires_linked_entity;

-- =============================================================================
-- Duplicate Management
-- =============================================================================

CREATE TABLE IF NOT EXISTS duplicate_dismissals (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL,
    entity_a_id   TEXT        NOT NULL,
    entity_b_id   TEXT        NOT NULL,
    dismissed_by  TEXT        NOT NULL,
    dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_dismissal UNIQUE (entity_type, entity_a_id, entity_b_id)
);

-- =============================================================================
-- Application Users & API Keys
-- =============================================================================

-- One row per exe.dev user; keyed by X-ExeDev-UserID. Upserted on each admin login.
CREATE TABLE IF NOT EXISTS app_users (
    id         TEXT        PRIMARY KEY,  -- X-ExeDev-UserID value
    email      TEXT        NOT NULL,     -- X-ExeDev-Email, updated on each login
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE TRIGGER trg_updated_at_app_users
    BEFORE UPDATE ON app_users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Hashed static API keys for programmatic access. Direct hard delete (no archive).
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT        PRIMARY KEY,         -- ULID
    user_id      TEXT        NOT NULL REFERENCES app_users(id),
    label        TEXT        NOT NULL,
    key_prefix   TEXT        NOT NULL,            -- first 8 chars of raw key, for display
    key_hash     TEXT        NOT NULL UNIQUE,     -- SHA-256 hex of raw key
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);

-- =============================================================================
-- API Key Scopes
-- =============================================================================

-- Defines valid scope identifiers for API key access control.
CREATE TABLE IF NOT EXISTS api_key_scope_types (
    id           TEXT PRIMARY KEY,   -- e.g. 'observations:write'
    display_name TEXT NOT NULL,
    description  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_key_scopes (
    api_key_id   TEXT        NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    scope_id     TEXT        NOT NULL REFERENCES api_key_scope_types(id) ON DELETE RESTRICT,
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by   TEXT        REFERENCES app_users(id) ON DELETE SET NULL,
    PRIMARY KEY (api_key_id, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_api_key_scopes_scope
    ON api_key_scopes(scope_id);

-- Seed built-in scope types.
INSERT INTO api_key_scope_types (id, display_name, description) VALUES
    ('observations:write',
     'Observations: Write',
     'Submit identity observations via POST /api/v1/observations')
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- API Request Log (#260)
-- =============================================================================

-- Append-only observability log of public API traffic. One row per /api/v1/*
-- request, written by the pure-ASGI capture middleware (src/api/public/
-- middleware.py). Structured metadata columns support filtering/aggregation;
-- raw request/response bodies support debug drill-down. Both are pruned on the
-- same 90-day window as the entity_changes outbox by scripts/prune_outbox.py
-- (daily power-map-prune.timer, issue #204).
--
-- result_entity_id intentionally has NO foreign key: the referenced entity may
-- be hard-deleted or merged, and the historical log record must survive. The
-- admin UI resolves it to a link and shows "(removed)" on a 404.
CREATE TABLE IF NOT EXISTS api_request_log (
    id               BIGSERIAL   PRIMARY KEY,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id       TEXT        REFERENCES api_keys(id) ON DELETE SET NULL,  -- NULL = unauthenticated/invalid
    method           TEXT        NOT NULL,
    path             TEXT        NOT NULL,
    route_group      TEXT        NOT NULL,          -- 'observations' | 'changes' | 'other'
    entity_type      TEXT,                          -- observations: people|organizations|jurisdictions
    status_code      INT         NOT NULL,
    latency_ms       INT         NOT NULL,
    disposition      TEXT,                          -- observations: new|auto-attached|rejected
    result_entity_id TEXT,                          -- observations: matched/created entity (no FK — may be deleted/merged)
    reason           TEXT,                          -- rejection/error reason code
    item_count       INT,                           -- changes: rows delivered (0 = empty poll)
    is_empty         BOOLEAN     NOT NULL DEFAULT FALSE,  -- changes: zero-result poll flag
    client_ip        TEXT,
    user_agent       TEXT,
    request_body     JSONB,                         -- raw; NULL for GET/changes
    response_body    JSONB                          -- raw
);

CREATE INDEX IF NOT EXISTS idx_arl_occurred
    ON api_request_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_arl_key_occurred
    ON api_request_log (api_key_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_arl_group_occurred
    ON api_request_log (route_group, occurred_at DESC);
-- Partial index for the "problems" view (rejections + 4xx/5xx).
CREATE INDEX IF NOT EXISTS idx_arl_problems
    ON api_request_log (occurred_at DESC)
    WHERE status_code >= 400 OR disposition = 'rejected';

-- =============================================================================
-- Entity Events (#170)
-- =============================================================================

CREATE TABLE IF NOT EXISTS entity_events (
    id                      TEXT PRIMARY KEY,
    entity_type             TEXT NOT NULL CHECK (entity_type IN ('person', 'organization')),
    entity_id               TEXT NOT NULL,
    event_type_id           TEXT NOT NULL REFERENCES entity_event_types(id),

    event_year              INTEGER CHECK (event_year BETWEEN -9999 AND 9999),
    event_month             INTEGER CHECK (event_month BETWEEN 1 AND 12),
    event_day               INTEGER CHECK (event_day BETWEEN 1 AND 31),
    event_hour              INTEGER CHECK (event_hour BETWEEN 0 AND 23),
    event_minute            INTEGER CHECK (event_minute BETWEEN 0 AND 59),
    event_second            INTEGER CHECK (event_second BETWEEN 0 AND 59),
    event_at                TIMESTAMPTZ,

    event_place_text        TEXT,
    event_place_address_id  TEXT REFERENCES addresses(id) ON DELETE SET NULL,

    linked_entity_type      TEXT CHECK (linked_entity_type IN ('person', 'organization')),
    linked_entity_id        TEXT,

    notes                   TEXT,
    visibility              TEXT NOT NULL DEFAULT 'public'
                            CHECK (visibility IN ('public', 'legal_only', 'hidden')),
    source_key_id           TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
    verified_at             TIMESTAMPTZ,
    archived_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_month_requires_year    CHECK (event_month  IS NULL OR event_year   IS NOT NULL),
    CONSTRAINT chk_day_requires_month     CHECK (event_day    IS NULL OR event_month  IS NOT NULL),
    CONSTRAINT chk_hour_requires_day      CHECK (event_hour   IS NULL OR event_day    IS NOT NULL),
    CONSTRAINT chk_minute_requires_hour   CHECK (event_minute IS NULL OR event_hour   IS NOT NULL),
    CONSTRAINT chk_second_requires_minute CHECK (event_second IS NULL OR event_minute IS NOT NULL),
    CONSTRAINT chk_linked_entity_pair     CHECK (
        (linked_entity_type IS NULL) = (linked_entity_id IS NULL)
    ),
    CONSTRAINT chk_at_requires_year      CHECK (event_at IS NULL OR event_year IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_entity_events_entity
    ON entity_events(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_events_type
    ON entity_events(event_type_id);

CREATE INDEX IF NOT EXISTS idx_entity_events_entity_active
    ON entity_events(entity_type, entity_id)
    WHERE archived_at IS NULL;

CREATE OR REPLACE TRIGGER trg_updated_at_entity_events
    BEFORE UPDATE ON entity_events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE FUNCTION touch_parent_on_entity_event_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_entity_type TEXT;
    v_entity_id   TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_entity_type := OLD.entity_type;
        v_entity_id   := OLD.entity_id;
    ELSE
        v_entity_type := NEW.entity_type;
        v_entity_id   := NEW.entity_id;
    END IF;

    IF v_entity_type = 'person' THEN
        UPDATE people SET updated_at = NOW() WHERE id = v_entity_id;
    ELSIF v_entity_type = 'organization' THEN
        UPDATE organizations SET updated_at = NOW() WHERE id = v_entity_id;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE TRIGGER trg_touch_entity_on_event_change
    AFTER INSERT OR UPDATE OR DELETE ON entity_events
    FOR EACH ROW EXECUTE FUNCTION touch_parent_on_entity_event_change();

-- =============================================================================
-- Ingestion Audit Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS import_batches (
    id           TEXT        PRIMARY KEY,
    source_file  TEXT        NOT NULL,
    file_hash    TEXT        NOT NULL,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    imported_by  TEXT,
    row_count    INTEGER     NOT NULL CHECK (row_count >= 0),
    loaded_count INTEGER     NOT NULL CHECK (loaded_count >= 0),
    error_count  INTEGER     NOT NULL CHECK (error_count >= 0),
    notes        TEXT
);

-- Idempotent: adds unique constraint on existing tables that predate this column.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'import_batches_file_hash_key'
          AND conrelid = 'import_batches'::regclass
    ) THEN
        ALTER TABLE import_batches ADD CONSTRAINT import_batches_file_hash_key
            UNIQUE (file_hash);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS import_provenance (
    id              TEXT        PRIMARY KEY,
    batch_id        TEXT        NOT NULL REFERENCES import_batches(id),
    source_row      INTEGER     NOT NULL,
    entity_type     TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction')),
    entity_id       TEXT        NOT NULL,
    action          TEXT        NOT NULL CHECK (action IN ('created','matched','skipped','error')),
    error_detail    JSONB,
    raw_data        JSONB       NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_provenance_batch
    ON import_provenance(batch_id);

CREATE INDEX IF NOT EXISTS idx_import_provenance_entity
    ON import_provenance(entity_type, entity_id);

-- Append-only: never UPDATE, always INSERT to preserve history.
-- Latest assessment: ORDER BY assessed_at DESC LIMIT 1.
CREATE TABLE IF NOT EXISTS field_confidence (
    id                  TEXT        PRIMARY KEY,
    entity_type         TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction')),
    entity_id           TEXT        NOT NULL,
    field_name          TEXT        NOT NULL,
    value_hash          TEXT        NOT NULL,
    source_reliability  REAL        NOT NULL CHECK (source_reliability BETWEEN 0.0 AND 1.0),
    validation_status   TEXT        NOT NULL CHECK (validation_status IN (
                            'confirmed', 'unconfirmed', 'failed', 'not_attempted')),
    validation_detail   JSONB,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    assessed_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_field_confidence_entity
    ON field_confidence(entity_type, entity_id, field_name);

-- Migration: add normalizer enrichment columns to addresses
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS latitude   DOUBLE PRECISION;
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS longitude  DOUBLE PRECISION;
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS components JSONB;

-- Migration: drop DEFAULT 'US' from addresses.country
-- Existing rows keep their 'US' value; application layer now always provides country explicitly.
ALTER TABLE addresses ALTER COLUMN country DROP DEFAULT;

-- Migration (#162): per-name provenance — which API key sourced a name row.
-- NULL for pre-observation-API rows; populated by observation writers going forward.
ALTER TABLE person_names
    ADD COLUMN IF NOT EXISTS source_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL;
ALTER TABLE organization_names
    ADD COLUMN IF NOT EXISTS source_key_id TEXT REFERENCES api_keys(id) ON DELETE SET NULL;

-- Migration (#162 CR): drop redundant api_key_scopes_key index — the PK on
-- (api_key_id, scope_id) already supports lookups by api_key_id alone.
DROP INDEX IF EXISTS idx_api_key_scopes_key;

-- Migration (#163): change feed — deleted_entities tombstone + updated_at indexes.

CREATE TABLE IF NOT EXISTS deleted_entities (
    entity_type  TEXT        NOT NULL CHECK (entity_type IN ('person', 'organization', 'jurisdiction', 'role', 'role_assignment')),
    entity_id    TEXT        NOT NULL,
    deleted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_type, entity_id)
);

-- TTL cleanup (issue #204): rows older than 90 days are pruned alongside the
-- entity_changes outbox by scripts/prune_outbox.py (daily power-map-prune.timer).

CREATE INDEX IF NOT EXISTS idx_deleted_entities_deleted_at
    ON deleted_entities (deleted_at ASC);

CREATE INDEX IF NOT EXISTS idx_people_updated_at
    ON people (updated_at ASC);

CREATE INDEX IF NOT EXISTS idx_organizations_updated_at
    ON organizations (updated_at ASC);

-- =============================================================================
-- Migration (#168): extend entity_type CHECK constraints to include 'jurisdiction'
-- Pattern: check if constraint exists and lacks 'jurisdiction', then drop + re-add.
-- The CREATE TABLE IF NOT EXISTS definitions above already carry the new shape for
-- fresh databases; these blocks handle existing databases.
-- =============================================================================

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'entity_addresses_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE entity_addresses DROP CONSTRAINT entity_addresses_entity_type_check;
        ALTER TABLE entity_addresses ADD CONSTRAINT entity_addresses_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'jurisdiction'));
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'contact_methods_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE contact_methods DROP CONSTRAINT contact_methods_entity_type_check;
        ALTER TABLE contact_methods ADD CONSTRAINT contact_methods_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction'));
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'links_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE links DROP CONSTRAINT links_entity_type_check;
        ALTER TABLE links ADD CONSTRAINT links_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment', 'jurisdiction'));
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'import_provenance_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE import_provenance DROP CONSTRAINT import_provenance_entity_type_check;
        ALTER TABLE import_provenance ADD CONSTRAINT import_provenance_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction'));
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'field_confidence_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE field_confidence DROP CONSTRAINT field_confidence_entity_type_check;
        ALTER TABLE field_confidence ADD CONSTRAINT field_confidence_entity_type_check
            CHECK (entity_type IN ('organization', 'person', 'role_assignment', 'jurisdiction'));
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'deleted_entities_entity_type_check'
          AND check_clause NOT LIKE '%jurisdiction%'
    ) THEN
        ALTER TABLE deleted_entities DROP CONSTRAINT deleted_entities_entity_type_check;
        ALTER TABLE deleted_entities ADD CONSTRAINT deleted_entities_entity_type_check
            CHECK (entity_type IN ('person', 'organization', 'jurisdiction'));
    END IF;
END $$;

-- =============================================================================
-- Migration (#277): extend deleted_entities entity_type CHECK to include
-- 'role' and 'role_assignment' so hard-deletes of those admin entities can emit
-- a tombstone → 'deleted' entity_changes signal (entity_changes already permits
-- both types). Same DROP + re-add idiom as #168; keyed on absence of 'role'.
-- The CREATE TABLE IF NOT EXISTS above carries the full shape for fresh DBs.
-- =============================================================================

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.check_constraints
        WHERE constraint_schema = 'public'
          AND constraint_name = 'deleted_entities_entity_type_check'
          AND check_clause NOT LIKE '%role%'
    ) THEN
        ALTER TABLE deleted_entities DROP CONSTRAINT deleted_entities_entity_type_check;
        ALTER TABLE deleted_entities ADD CONSTRAINT deleted_entities_entity_type_check
            CHECK (entity_type IN
                ('person', 'organization', 'jurisdiction', 'role', 'role_assignment'));
    END IF;
END $$;

-- Migration (#170): add precision tier to addresses for event-place and historical records.
ALTER TABLE addresses
    ADD COLUMN IF NOT EXISTS precision TEXT
        CHECK (precision IN ('country', 'region', 'city', 'postal', 'street'));

-- Migration (#176): roles as first-class public-API entities.
-- contact_methods: add 'role' so roles can carry email/phone.
-- Use DROP CONSTRAINT IF EXISTS + ADD (idempotent; only adds values, never removes).
ALTER TABLE contact_methods DROP CONSTRAINT IF EXISTS contact_methods_entity_type_check;
ALTER TABLE contact_methods ADD CONSTRAINT contact_methods_entity_type_check
    CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment', 'jurisdiction'));

-- entity_addresses: restore 'role_assignment' (dropped by prior migration); keep 'role'.
ALTER TABLE entity_addresses DROP CONSTRAINT IF EXISTS entity_addresses_entity_type_check;
ALTER TABLE entity_addresses ADD CONSTRAINT entity_addresses_entity_type_check
    CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment', 'jurisdiction'));

-- =============================================================================
-- Migration (#188): voice embeddings — pgvector, embedding model registry,
--                   pyannote-community-1-embed table, new API scopes.
-- =============================================================================

-- Requires the postgresql-*-pgvector OS package and superuser to create the
-- first time; idempotent on subsequent apply_schema runs.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Embedding model registry
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE vector_metric AS ENUM ('cosine', 'l2', 'inner_product');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS embedding_model_registry (
    model_id                  TEXT        PRIMARY KEY,
    table_name                VARCHAR(63) NOT NULL UNIQUE,
    dimension                 INT         NOT NULL,
    metric                    vector_metric NOT NULL,
    accepts_writes            BOOLEAN     NOT NULL DEFAULT true,
    is_queryable              BOOLEAN     NOT NULL DEFAULT true,
    is_default_for_enrollment BOOLEAN     NOT NULL DEFAULT false,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    deprecated_at             TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS embedding_model_registry_default_enrollment_idx
    ON embedding_model_registry (is_default_for_enrollment)
    WHERE is_default_for_enrollment = true;

INSERT INTO embedding_model_registry
    (model_id, table_name, dimension, metric, is_default_for_enrollment)
VALUES
    ('pyannote-community-1-embed',
     'person_embeddings_pyannote_community_1_embed',
     192,  -- corrected to 256 by migration #197
     'cosine',
     true)
ON CONFLICT (model_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- pyannote-community-1-embed embeddings table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS person_embeddings_pyannote_community_1_embed (
    id                   CHAR(26)    PRIMARY KEY,
    person_id            CHAR(26)    NOT NULL REFERENCES people(id) ON DELETE CASCADE,

    embedding            vector(192) NOT NULL,  -- corrected to vector(256) by migration #197
    embedding_dim        INT         NOT NULL CHECK (embedding_dim = 192),  -- check corrected to 256 by migration #197

    activity_ms          INT         NOT NULL CHECK (activity_ms >= 0),
    audio_sample_rate_hz INT         NOT NULL CHECK (audio_sample_rate_hz > 0),
    source_service       TEXT        NOT NULL,
    source_job_id        TEXT        NOT NULL,
    source_segment       INT         NOT NULL,
    recorded_at          TIMESTAMPTZ NOT NULL,

    created_by_key_id    CHAR(26)    NOT NULL REFERENCES api_keys(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at          TIMESTAMPTZ,
    meta                 JSONB       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT person_embeddings_pyannote_community_1_embed_source_unique
        UNIQUE (source_service, source_job_id, source_segment, person_id)
);

DO $$ BEGIN
    ALTER TABLE person_embeddings_pyannote_community_1_embed
        ADD CONSTRAINT person_embeddings_pyannote_community_1_embed_sample_rate_check
        CHECK (audio_sample_rate_hz > 0);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS person_embeddings_pyannote_community_1_embed_person_id_idx
    ON person_embeddings_pyannote_community_1_embed (person_id)
    WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS person_embeddings_pyannote_community_1_embed_source_job_id_idx
    ON person_embeddings_pyannote_community_1_embed (source_job_id);

CREATE INDEX IF NOT EXISTS person_embeddings_pyannote_community_1_embed_hnsw
    ON person_embeddings_pyannote_community_1_embed
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE archived_at IS NULL;

-- ---------------------------------------------------------------------------
-- New API scopes
-- ---------------------------------------------------------------------------

-- Descriptions superseded by migration #299 below (list + verify endpoints added).
INSERT INTO api_key_scope_types (id, display_name, description) VALUES
    ('voice_embeddings:write',
     'Voice Embeddings: Write',
     'Write voice embedding observations via POST /api/v1/people/{id}/embeddings'),
    ('voice_embeddings:read',
     'Voice Embeddings: Read',
     'Query persons by voice embedding similarity via POST /api/v1/people/identify')
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- Migration (#194): organization_jurisdiction_affiliation_types seed
-- =============================================================================

INSERT INTO organization_jurisdiction_affiliation_types (id, slug, display_name) VALUES
    ('01KW0000000000000000000001', 'governing',  'is governed by'),
    ('01KW0000000000000000000002', 'registered', 'is registered in')
ON CONFLICT (id) DO UPDATE SET
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name;

-- =============================================================================
-- Migration (#197): pyannote-community-1-embed dimensionality fix 192 → 256
--
-- The upstream pyannote/speaker-diarization-community-1 pipeline (whisperx
-- 3.8.5) emits 256-D embeddings; the registry was seeded at 192-D, rejecting
-- every write since #188 shipped.  Zero 192-D rows exist in the live table.
-- =============================================================================

-- Drop HNSW index first — pgvector requires this before ALTER COLUMN TYPE
DROP INDEX IF EXISTS person_embeddings_pyannote_community_1_embed_hnsw;

-- Drop the 192-D check constraint (auto-name truncated to 63 chars by PG)
DO $$ BEGIN
    ALTER TABLE person_embeddings_pyannote_community_1_embed
        DROP CONSTRAINT person_embeddings_pyannote_community_1_embe_embedding_dim_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- Change vector column type (safe: no rows exist)
ALTER TABLE person_embeddings_pyannote_community_1_embed
    ALTER COLUMN embedding TYPE vector(256);

-- New check constraint with a controlled short name
DO $$ BEGIN
    ALTER TABLE person_embeddings_pyannote_community_1_embed
        ADD CONSTRAINT pep_community1_embed_dim_check CHECK (embedding_dim = 256);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Bump registry row (INSERT … ON CONFLICT DO NOTHING already ran; use UPDATE)
UPDATE embedding_model_registry
    SET dimension = 256
    WHERE model_id = 'pyannote-community-1-embed';

-- =============================================================================
-- Migration (#203): outbox log + per-key entity subscription filter on /changes
--
-- Replaces the timestamp-cursor polling pattern with:
--   1. entity_changes — append-only outbox with BIGSERIAL cursor
--   2. api_key_entity_subscriptions — explicit per-key entity allowlist
--
-- Breaking changes:
--   - /changes ?since= replaced by ?after= (integer seq_id, > exclusive)
--   - /changes always filtered by subscription set; empty set → empty feed
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Outbox log
--
-- Append-only; not self-limiting. Rows older than 90 days are pruned by
-- scripts/prune_outbox.py (daily power-map-prune.timer, issue #204), so the
-- public change feed is a recent-changes window, not a permanent event store.
-- Pruning is a DELETE by changed_at; no dedicated index is kept here to avoid
-- taxing the trigger-heavy insert path — revisit range-partitioning if volume
-- makes the daily scan expensive.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS entity_changes (
    id           BIGSERIAL    PRIMARY KEY,
    entity_type  TEXT         NOT NULL
                              CHECK (entity_type IN
                                ('person', 'organization', 'jurisdiction',
                                 'role', 'role_assignment')),
    entity_id    TEXT         NOT NULL,
    change_kind  TEXT         NOT NULL
                              CHECK (change_kind IN ('updated', 'deleted')),
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Supports the subscription-join query: entity_id lookup within id range.
CREATE INDEX IF NOT EXISTS idx_entity_changes_entity
    ON entity_changes (entity_id, id);

-- ---------------------------------------------------------------------------
-- Trigger function: fire on INSERT OR UPDATE of entity tables → outbox row
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_record_entity_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO entity_changes (entity_type, entity_id, change_kind)
    VALUES (
        CASE TG_TABLE_NAME
            WHEN 'people'            THEN 'person'
            WHEN 'organizations'     THEN 'organization'
            WHEN 'jurisdictions'     THEN 'jurisdiction'
            WHEN 'roles'             THEN 'role'
            WHEN 'role_assignments'  THEN 'role_assignment'
        END,
        NEW.id,
        'updated'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_entity_changes_people
    AFTER INSERT OR UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION fn_record_entity_change();

CREATE OR REPLACE TRIGGER trg_entity_changes_organizations
    AFTER INSERT OR UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION fn_record_entity_change();

CREATE OR REPLACE TRIGGER trg_entity_changes_jurisdictions
    AFTER INSERT OR UPDATE ON jurisdictions
    FOR EACH ROW EXECUTE FUNCTION fn_record_entity_change();

CREATE OR REPLACE TRIGGER trg_entity_changes_roles
    AFTER INSERT OR UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION fn_record_entity_change();

CREATE OR REPLACE TRIGGER trg_entity_changes_role_assignments
    AFTER INSERT OR UPDATE ON role_assignments
    FOR EACH ROW EXECUTE FUNCTION fn_record_entity_change();

-- ---------------------------------------------------------------------------
-- Trigger function: deleted_entities INSERT → outbox tombstone row
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_record_deleted_entity_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO entity_changes (entity_type, entity_id, change_kind)
    VALUES (NEW.entity_type, NEW.entity_id, 'deleted');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_entity_changes_deleted
    AFTER INSERT ON deleted_entities
    FOR EACH ROW EXECUTE FUNCTION fn_record_deleted_entity_change();

-- ---------------------------------------------------------------------------
-- Subscription allowlist
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS api_key_entity_subscriptions (
    api_key_id   TEXT         NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    entity_id    TEXT         NOT NULL,
    entity_type  TEXT         NOT NULL
                              CHECK (entity_type IN
                                ('person', 'organization', 'jurisdiction',
                                 'role', 'role_assignment')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_key_id, entity_id)
);

-- Supports the subscription-join query from the changes endpoint.
CREATE INDEX IF NOT EXISTS idx_sub_entity
    ON api_key_entity_subscriptions (entity_id, api_key_id);

-- Backs GET /api/v1/subscriptions list ordering: WHERE api_key_id ORDER BY
-- created_at, entity_id (#297). Lets the paged scan avoid a sort step as a
-- key's subscription set grows.
CREATE INDEX IF NOT EXISTS idx_sub_key_created
    ON api_key_entity_subscriptions (api_key_id, created_at, entity_id);

-- ---------------------------------------------------------------------------
-- New scope: subscriptions:write
-- ---------------------------------------------------------------------------

INSERT INTO api_key_scope_types (id, display_name, description) VALUES
    ('subscriptions:write',
     'Subscriptions: Write',
     'Manage entity subscriptions via POST/DELETE /api/v1/subscriptions')
ON CONFLICT (id) DO NOTHING;

-- Recreate HNSW index for 256-D cosine
CREATE INDEX IF NOT EXISTS person_embeddings_pyannote_community_1_embed_hnsw
    ON person_embeddings_pyannote_community_1_embed
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE archived_at IS NULL;

-- =============================================================================
-- Migration (#201): FTS search infrastructure
-- Adds pm_simple / pm_unaccent_simple TS configs, search_tsv columns on
-- organizations, people, roles, and jurisdictions, maintaining GIN indexes,
-- and trigram GIN indexes on name columns for admin typeaheads.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------------------
-- Text search configurations
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TEXT SEARCH CONFIGURATION pm_simple (COPY = simple);
EXCEPTION WHEN unique_violation OR duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TEXT SEARCH DICTIONARY pm_unaccent (
        TEMPLATE = unaccent,
        RULES    = 'unaccent'
    );
EXCEPTION WHEN unique_violation OR duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TEXT SEARCH CONFIGURATION pm_unaccent_simple (COPY = simple);
EXCEPTION WHEN unique_violation OR duplicate_object THEN NULL;
END $$;

-- Wire unaccent into pm_unaccent_simple for word tokens.
-- Idempotent: ALTER MAPPING replaces any existing mapping for these token types.
ALTER TEXT SEARCH CONFIGURATION pm_unaccent_simple
    ALTER MAPPING FOR hword, hword_part, word
    WITH pm_unaccent, simple;

-- ---------------------------------------------------------------------------
-- organizations.search_tsv
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE organizations ADD COLUMN search_tsv tsvector;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_organizations_search_tsv
    ON organizations USING GIN (search_tsv);

-- Shared refresh function for child-table triggers (organization_names,
-- organization_acronyms). Uses NEW.organization_id / OLD.organization_id.
CREATE OR REPLACE FUNCTION fn_refresh_org_search_tsv_from_child()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id TEXT;
BEGIN
    v_id := COALESCE(NEW.organization_id, OLD.organization_id);
    UPDATE organizations SET search_tsv = (
        -- Two independent LEFT JOINs produce a names × acronyms Cartesian product;
        -- DISTINCT inside each string_agg deduplicates before building the tsvector.
        SELECT
            setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT n.name,    ' '), '')), 'A') ||
            setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT a.acronym, ' '), '')), 'B') ||
            setweight(to_tsvector('pm_simple', COALESCE(o.notes, '')),                             'C')
        FROM organizations o
        LEFT JOIN organization_names    n ON n.organization_id = o.id
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id
        WHERE o.id = v_id
        GROUP BY o.id, o.notes
    ) WHERE id = v_id;
    RETURN NULL;
END;
$$;

-- Refresh triggered by notes change on the org row itself. Uses NEW.id.
CREATE OR REPLACE FUNCTION fn_refresh_org_search_tsv_from_org()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE organizations SET search_tsv = (
        SELECT
            setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT n.name,    ' '), '')), 'A') ||
            setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT a.acronym, ' '), '')), 'B') ||
            setweight(to_tsvector('pm_simple', COALESCE(NEW.notes, '')),                           'C')
        FROM organizations o
        LEFT JOIN organization_names    n ON n.organization_id = o.id
        LEFT JOIN organization_acronyms a ON a.organization_id = o.id
        WHERE o.id = NEW.id
        GROUP BY o.id
    ) WHERE id = NEW.id;
    RETURN NULL;
END;
$$;

DO $$ BEGIN
    CREATE TRIGGER trg_org_names_search_tsv
        AFTER INSERT OR UPDATE OR DELETE ON organization_names
        FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv_from_child();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_org_acronyms_search_tsv
        AFTER INSERT OR UPDATE OR DELETE ON organization_acronyms
        FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv_from_child();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_org_notes_search_tsv
        AFTER UPDATE OF notes ON organizations
        FOR EACH ROW EXECUTE FUNCTION fn_refresh_org_search_tsv_from_org();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backfill existing org rows.
UPDATE organizations o SET search_tsv = (
    SELECT
        setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT n.name,    ' '), '')), 'A') ||
        setweight(to_tsvector('pm_simple', COALESCE(string_agg(DISTINCT a.acronym, ' '), '')), 'B') ||
        setweight(to_tsvector('pm_simple', COALESCE(o2.notes, '')),                            'C')
    FROM organizations o2
    LEFT JOIN organization_names    n ON n.organization_id = o2.id
    LEFT JOIN organization_acronyms a ON a.organization_id = o2.id
    WHERE o2.id = o.id
    GROUP BY o2.id, o2.notes
)
WHERE search_tsv IS NULL;

-- ---------------------------------------------------------------------------
-- people.search_tsv
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE people ADD COLUMN search_tsv tsvector;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_people_search_tsv
    ON people USING GIN (search_tsv);

CREATE OR REPLACE FUNCTION fn_refresh_person_search_tsv_from_child()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_id TEXT;
BEGIN
    v_id := COALESCE(NEW.person_id, OLD.person_id);
    UPDATE people SET search_tsv = (
        SELECT
            setweight(to_tsvector('pm_unaccent_simple', COALESCE(string_agg(pn.name, ' '), '')), 'A') ||
            setweight(to_tsvector('pm_unaccent_simple', COALESCE(p.notes, '')),                  'C')
        FROM people p
        LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.visibility = 'public'
        WHERE p.id = v_id
        GROUP BY p.id, p.notes
    ) WHERE id = v_id;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_refresh_person_search_tsv_from_person()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE people SET search_tsv = (
        SELECT
            setweight(to_tsvector('pm_unaccent_simple', COALESCE(string_agg(pn.name, ' '), '')), 'A') ||
            setweight(to_tsvector('pm_unaccent_simple', COALESCE(NEW.notes, '')),                'C')
        FROM people p
        LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.visibility = 'public'
        WHERE p.id = NEW.id
        GROUP BY p.id
    ) WHERE id = NEW.id;
    RETURN NULL;
END;
$$;

DO $$ BEGIN
    CREATE TRIGGER trg_person_names_search_tsv
        AFTER INSERT OR UPDATE OR DELETE ON person_names
        FOR EACH ROW EXECUTE FUNCTION fn_refresh_person_search_tsv_from_child();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_person_notes_search_tsv
        AFTER UPDATE OF notes ON people
        FOR EACH ROW EXECUTE FUNCTION fn_refresh_person_search_tsv_from_person();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backfill existing person rows.
UPDATE people p SET search_tsv = (
    SELECT
        setweight(to_tsvector('pm_unaccent_simple', COALESCE(string_agg(pn.name, ' '), '')), 'A') ||
        setweight(to_tsvector('pm_unaccent_simple', COALESCE(p2.notes, '')),                 'C')
    FROM people p2
    LEFT JOIN person_names pn ON pn.person_id = p2.id AND pn.visibility = 'public'
    WHERE p2.id = p.id
    GROUP BY p2.id, p2.notes
)
WHERE search_tsv IS NULL;

-- ---------------------------------------------------------------------------
-- roles.search_tsv  (Pattern A — single-table BEFORE trigger)
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE roles ADD COLUMN search_tsv tsvector;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_roles_search_tsv
    ON roles USING GIN (search_tsv);

CREATE OR REPLACE FUNCTION fn_roles_search_tsv()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('pm_simple', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('pm_simple', COALESCE(NEW.notes, '')), 'B');
    RETURN NEW;
END;
$$;

DO $$ BEGIN
    CREATE TRIGGER trg_roles_search_tsv
        BEFORE INSERT OR UPDATE OF title, notes ON roles
        FOR EACH ROW EXECUTE FUNCTION fn_roles_search_tsv();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backfill existing role rows.
UPDATE roles SET search_tsv =
    setweight(to_tsvector('pm_simple', COALESCE(title, '')), 'A') ||
    setweight(to_tsvector('pm_simple', COALESCE(notes, '')), 'B')
WHERE search_tsv IS NULL;

-- ---------------------------------------------------------------------------
-- jurisdictions.search_tsv  (Pattern A — single-table BEFORE trigger)
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    ALTER TABLE jurisdictions ADD COLUMN search_tsv tsvector;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_jurisdictions_search_tsv
    ON jurisdictions USING GIN (search_tsv);

CREATE OR REPLACE FUNCTION fn_jurisdictions_search_tsv()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('pm_simple', COALESCE(NEW.name,  '')), 'A') ||
        setweight(to_tsvector('pm_simple', COALESCE(NEW.slug,  '')), 'B') ||
        setweight(to_tsvector('pm_simple', COALESCE(NEW.notes, '')), 'C');
    RETURN NEW;
END;
$$;

DO $$ BEGIN
    CREATE TRIGGER trg_jurisdictions_search_tsv
        BEFORE INSERT OR UPDATE OF name, slug, notes ON jurisdictions
        FOR EACH ROW EXECUTE FUNCTION fn_jurisdictions_search_tsv();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Backfill existing jurisdiction rows.
UPDATE jurisdictions SET search_tsv =
    setweight(to_tsvector('pm_simple', COALESCE(name,  '')), 'A') ||
    setweight(to_tsvector('pm_simple', COALESCE(slug,  '')), 'B') ||
    setweight(to_tsvector('pm_simple', COALESCE(notes, '')), 'C')
WHERE search_tsv IS NULL;

-- ---------------------------------------------------------------------------
-- Trigram GIN indexes for admin typeaheads (keep ILIKE behaviour)
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_org_names_name_trgm
    ON organization_names USING GIN (lower(name) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_person_names_name_trgm
    ON person_names USING GIN (lower(name) gin_trgm_ops)
    WHERE visibility = 'public';

CREATE INDEX IF NOT EXISTS idx_roles_title_trgm
    ON roles USING GIN (lower(title) gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Trigram GiST indexes for similarity() dup-detection joins
-- GIN (above) accelerates ILIKE/@@; GiST is needed for similarity() scans.
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_org_names_name_gist_trgm
    ON organization_names USING GIST (name gist_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_person_names_name_gist_trgm
    ON person_names USING GIST (name gist_trgm_ops)
    WHERE visibility = 'public';

-- ---------------------------------------------------------------------------
-- Partial indexes on archived_at IS NULL for active-row queries
--
-- Used by dashboard/entities COUNT(*) subqueries and list-page filters.
-- Partial: only active rows are indexed; archiving a row removes it.
-- apply_schema creates these CONCURRENTLY (outside the transaction).
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_people_archived_at
    ON people (archived_at) WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_organizations_archived_at
    ON organizations (archived_at) WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_roles_archived_at
    ON roles (archived_at) WHERE archived_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_role_assignments_archived_at
    ON role_assignments (archived_at) WHERE archived_at IS NULL;

-- ---------------------------------------------------------------------------
-- Shared dup-count cache (multi-worker safe)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dup_count_cache (
    entity_type  TEXT        PRIMARY KEY,  -- 'organization' | 'person'
    count        INT         NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Unique constraint: contact_methods (entity_type, entity_id, contact_type, value)
--
-- Prevents merge bugs from silently producing duplicate rows.
-- Clean up any pre-existing duplicates (keep oldest row) before adding.
-- ---------------------------------------------------------------------------

DELETE FROM contact_methods a USING contact_methods b
WHERE a.id > b.id
  AND a.entity_type = b.entity_type
  AND a.entity_id   = b.entity_id
  AND a.contact_type = b.contact_type
  AND a.value       = b.value;

ALTER TABLE contact_methods DROP CONSTRAINT IF EXISTS contact_methods_entity_contact_uniq;
ALTER TABLE contact_methods
    ADD CONSTRAINT contact_methods_entity_contact_uniq
    UNIQUE (entity_type, entity_id, contact_type, value);

-- ---------------------------------------------------------------------------
-- Migration (#181): temporal validity window on entity_addresses.
-- NULL on either side = open-ended. Must run before the unique-constraint
-- block below, which references the new columns.
-- ---------------------------------------------------------------------------

ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS valid_from  DATE;
ALTER TABLE entity_addresses ADD COLUMN IF NOT EXISTS valid_until DATE;

ALTER TABLE entity_addresses DROP CONSTRAINT IF EXISTS chk_ea_validity;
ALTER TABLE entity_addresses
    ADD CONSTRAINT chk_ea_validity
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until);

-- ---------------------------------------------------------------------------
-- Unique constraint: entity_addresses
--   (entity_type, entity_id, address_type, address_id, valid_from, valid_until)
--
-- Closes the FK-level duplicate hole produced by merge. Normalised-form dedup
-- (same physical address, different addresses rows) is handled at write time by
-- write_addresses() via COALESCE(standardized, raw_input); this constraint
-- catches exact-FK duplicates that _execute_merge could produce.
--
-- #181: the validity window is part of the key — the same entity at the same
-- address across two windows is legitimate (HQ moves back). NULLS NOT DISTINCT
-- keeps duplicate open-ended (NULL/NULL) rows blocked, preserving the original
-- guarantee. The dedup DELETE uses IS NOT DISTINCT FROM for the same reason —
-- it must never collapse rows that differ only in their validity window.
-- ---------------------------------------------------------------------------

DELETE FROM entity_addresses a USING entity_addresses b
WHERE a.id > b.id
  AND a.entity_type  = b.entity_type
  AND a.entity_id    = b.entity_id
  AND a.address_type = b.address_type
  AND a.address_id   = b.address_id
  AND a.valid_from   IS NOT DISTINCT FROM b.valid_from
  AND a.valid_until  IS NOT DISTINCT FROM b.valid_until;

ALTER TABLE entity_addresses DROP CONSTRAINT IF EXISTS entity_addresses_entity_addr_uniq;
ALTER TABLE entity_addresses
    ADD CONSTRAINT entity_addresses_entity_addr_uniq
    UNIQUE NULLS NOT DISTINCT
        (entity_type, entity_id, address_type, address_id, valid_from, valid_until);

-- Migration (#235): carry merged_into (winner id) on merge tombstones.
-- NULL = genuine delete; non-NULL = merged into winner.

ALTER TABLE deleted_entities
    ADD COLUMN IF NOT EXISTS merged_into TEXT NULL;

ALTER TABLE entity_changes
    ADD COLUMN IF NOT EXISTS merged_into TEXT NULL;

ALTER TABLE entity_changes DROP CONSTRAINT IF EXISTS entity_changes_merged_into_kind_ck;
ALTER TABLE entity_changes
    ADD CONSTRAINT entity_changes_merged_into_kind_ck
    CHECK (merged_into IS NULL OR change_kind = 'deleted');

CREATE OR REPLACE FUNCTION fn_record_deleted_entity_change()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO entity_changes (entity_type, entity_id, change_kind, merged_into)
    VALUES (NEW.entity_type, NEW.entity_id, 'deleted', NEW.merged_into);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Migration (#299): voice_embeddings scope descriptions — name every gated route
-- =============================================================================
-- The original seed (#188) predates the embeddings list endpoint (#279) and
-- closed-set verify (#299). These descriptions surface in the admin
-- key-management UI, so they must state the full blast radius of a
-- biometric-data grant. Plain UPDATEs — the table has no updated_at trigger,
-- so re-application is naturally a no-op (CR #299 round 2).

UPDATE api_key_scope_types
   SET description = 'Read voice embedding data via POST /api/v1/people/identify, POST /api/v1/people/verify, and GET /api/v1/people/{id}/embeddings'
 WHERE id = 'voice_embeddings:read';

UPDATE api_key_scope_types
   SET description = 'Write, patch, archive (single + batch), and restore voice embedding observations via the /api/v1/people/{id}/embeddings endpoints'
 WHERE id = 'voice_embeddings:write';
