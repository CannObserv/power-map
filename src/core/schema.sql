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
                             CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,           -- short: "UBI", "SSN", "WA PDC"
    full_name    TEXT        NOT NULL,           -- long:  "Washington Unified Business Identifier"
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- Polymorphic join: links any entity to one or more addresses with a typed relationship
CREATE TABLE IF NOT EXISTS entity_addresses (
    id           TEXT        PRIMARY KEY,
    entity_type  TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person')),
    entity_id    TEXT        NOT NULL,
    address_id   TEXT        NOT NULL REFERENCES addresses(id),
    address_type TEXT        NOT NULL CHECK (address_type IN ('mailing', 'physical', 'other')),
    display_name TEXT,                           -- optional label, e.g. "Seattle Office"
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_addresses_entity
    ON entity_addresses(entity_type, entity_id);

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
                                 'reading','romanization','mrz'
                             )),
    is_canonical BOOLEAN     NOT NULL DEFAULT FALSE,

    -- i18n / cultural-awareness metadata (issue #121, Phase 1)
    locale              TEXT,                       -- BCP 47, e.g. 'en-US','zh-Hant-TW'
    script              TEXT,                       -- ISO 15924, e.g. 'Latn','Hant','Hans','Kana'
    sort_as             TEXT,                       -- explicit collation key; NULL → use `name`
    primary_identifier  TEXT
                        CHECK (primary_identifier IS NULL
                               OR primary_identifier IN ('family','given','patronymic','mononym')),
    visibility          TEXT NOT NULL DEFAULT 'public'
                        CHECK (visibility IN ('public','internal','legal_only','hidden')),
    reading_of_id       TEXT REFERENCES person_names(id),

    -- Structured parts (populated only when source provides; never auto-parsed)
    given_names         TEXT[],
    family_names        TEXT[],
    additional_names    TEXT[],
    honorific_prefix    TEXT,
    honorific_suffix    TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type)
    WHERE is_canonical = TRUE;

-- Display name view: canonical name for a person.
-- Used by all admin queries that show a person name for display (not editing).
CREATE OR REPLACE VIEW v_person_display_names AS
SELECT p.id AS person_id,
       n.name AS display_name
FROM people p
LEFT JOIN person_names n
    ON n.person_id = p.id AND n.is_canonical = TRUE
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
-- Polymorphic Tables
-- =============================================================================

-- Phone numbers and email addresses for any entity
CREATE TABLE IF NOT EXISTS contact_methods (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL
                              CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
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
                              CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment')),
    entity_id     TEXT        NOT NULL,
    url           TEXT        NOT NULL,
    link_type_id  TEXT        NOT NULL REFERENCES link_types(id),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_links_entity
    ON links(entity_type, entity_id);

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
-- Schema evolution: person_names i18n columns (issue #121, Phase 1)
-- All columns nullable except `visibility` (constant default 'public').
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='locale') THEN
        ALTER TABLE person_names ADD COLUMN locale TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='script') THEN
        ALTER TABLE person_names ADD COLUMN script TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='sort_as') THEN
        ALTER TABLE person_names ADD COLUMN sort_as TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='primary_identifier') THEN
        ALTER TABLE person_names ADD COLUMN primary_identifier TEXT
            CHECK (primary_identifier IS NULL
                   OR primary_identifier IN ('family','given','patronymic','mononym'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='visibility') THEN
        ALTER TABLE person_names ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
            CHECK (visibility IN ('public','internal','legal_only','hidden'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='reading_of_id') THEN
        ALTER TABLE person_names ADD COLUMN reading_of_id TEXT REFERENCES person_names(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='given_names') THEN
        ALTER TABLE person_names ADD COLUMN given_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='family_names') THEN
        ALTER TABLE person_names ADD COLUMN family_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='additional_names') THEN
        ALTER TABLE person_names ADD COLUMN additional_names TEXT[];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='honorific_prefix') THEN
        ALTER TABLE person_names ADD COLUMN honorific_prefix TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='person_names' AND column_name='honorific_suffix') THEN
        ALTER TABLE person_names ADD COLUMN honorific_suffix TEXT;
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
            'reading','romanization','mrz'
        )
    );
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

INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name) VALUES
    ('01KKZ3WGJSZF0F96SMYC000AVP', 'organization',    'org_ubi',       'UBI',    'Washington Unified Business Identifier'),
    ('01KKZ3WGJSZF0F96SMYC000AVQ', 'organization',    'org_wslcb',     'WSLCB',  'WA State Liquor and Cannabis Board License'),
    ('01KKZ3WGJSZF0F96SMYC000AVR', 'organization',    'org_wa_pdc',    'WA PDC', 'Washington State Public Disclosure Commission'),
    ('01KKZ3WGJSZF0F96SMYC000AVS', 'person',          'person_wa_pdc', 'WA PDC', 'Washington State Public Disclosure Commission'),
    ('01KKZ3WGJSZF0F96SMYC000AVT', 'person',          'person_ssn',    'SSN',    'United States Social Security Number'),
    ('01KKZ3WGJSZF0F96SMYC000AVV', 'role_assignment', 'role_wa_pdc',   'WA PDC', 'Washington State Public Disclosure Commission')
ON CONFLICT (id) DO UPDATE SET
    entity_type  = EXCLUDED.entity_type,
    slug         = EXCLUDED.slug,
    display_name = EXCLUDED.display_name,
    full_name    = EXCLUDED.full_name;

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
    entity_type     TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
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
    entity_type         TEXT        NOT NULL CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
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
