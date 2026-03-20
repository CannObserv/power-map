-- Power Map — canonical DDL
-- All PKs are ULIDs (TEXT). All timestamps are TIMESTAMPTZ.
-- Requires PostgreSQL 15+ (NULLS NOT DISTINCT on unique indexes).
-- Apply with: psql -f schema.sql

-- =============================================================================
-- Lookup / Reference Tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS platforms (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,    -- 'twitter', 'bluesky', 'linkedin', …
    display_name TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS url_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,    -- 'website', 'profile', 'wa_pdc', …
    display_name TEXT        NOT NULL,
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
    id             TEXT        PRIMARY KEY,
    raw_input      TEXT,                         -- original string before standardization
    standardized   TEXT,                         -- single-line form returned by API
    address_line_1 TEXT,
    address_line_2 TEXT,
    city           TEXT,
    region         TEXT,                         -- state / province
    postal_code    TEXT,
    country        TEXT        NOT NULL DEFAULT 'US',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

-- One row per name variant; exactly one row per (org, name_type) may have is_canonical = TRUE
CREATE TABLE IF NOT EXISTS organization_names (
    id              TEXT        PRIMARY KEY,
    organization_id TEXT        NOT NULL REFERENCES organizations(id),
    name            TEXT        NOT NULL,
    name_type       TEXT        NOT NULL DEFAULT 'legal'
                                CHECK (name_type IN ('legal', 'dba', 'former', 'acronym')),
    is_canonical    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_org_canonical_name
    ON organization_names(organization_id, name_type)
    WHERE is_canonical = TRUE;

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
                             CHECK (name_type IN ('legal', 'former', 'preferred', 'alias', 'initials')),
    is_canonical BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_person_canonical_name
    ON person_names(person_id, name_type)
    WHERE is_canonical = TRUE;

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

-- Web URLs for any entity; url_type_id references url_types (controlled vocabulary)
CREATE TABLE IF NOT EXISTS urls (
    id           TEXT        PRIMARY KEY,
    entity_type  TEXT        NOT NULL
                             CHECK (entity_type IN ('organization', 'person', 'role', 'role_assignment')),
    entity_id    TEXT        NOT NULL,
    url          TEXT        NOT NULL,
    url_type_id  TEXT        NOT NULL REFERENCES url_types(id),
    is_canonical BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_urls_entity ON urls(entity_type, entity_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_url_canonical
    ON urls(entity_type, entity_id)
    WHERE is_canonical = TRUE;

CREATE TABLE IF NOT EXISTS social_links (
    id          TEXT        PRIMARY KEY,
    entity_type TEXT        NOT NULL
                            CHECK (entity_type IN ('organization', 'person', 'role_assignment')),
    entity_id   TEXT        NOT NULL,
    platform_id TEXT        NOT NULL REFERENCES platforms(id),
    url         TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_links_entity
    ON social_links(entity_type, entity_id);

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
        SELECT 1 FROM information_schema.columns
        WHERE table_name='role_assignments' AND column_name='archived_at'
    ) THEN
        ALTER TABLE role_assignments ADD COLUMN archived_at TIMESTAMPTZ;
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
-- Seed Data
-- =============================================================================

INSERT INTO platforms (id, slug, display_name) VALUES
    ('01KKZ3WGJRPV2TDZV672NWFE8G', 'twitter',   'Twitter / X'),
    ('01KKZ3WGJRPV2TDZV672NWFE8H', 'bluesky',   'Bluesky'),
    ('01KKZ3WGJSZF0F96SMYC000AVA', 'linkedin',  'LinkedIn'),
    ('01KKZ3WGJSZF0F96SMYC000AVB', 'mastodon',  'Mastodon'),
    ('01KKZ3WGJSZF0F96SMYC000AVC', 'instagram', 'Instagram'),
    ('01KKZ3WGJSZF0F96SMYC000AVD', 'facebook',  'Facebook'),
    ('01KKZ3WGJSZF0F96SMYC000AVE', 'youtube',   'YouTube'),
    ('01KKZ3WGJSZF0F96SMYC000AVF', 'flickr',    'Flickr')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO url_types (id, slug, display_name) VALUES
    ('01KKZ3WGJSZF0F96SMYC000AVG', 'website',    'Official Website'),
    ('01KKZ3WGJSZF0F96SMYC000AVH', 'profile',    'Profile'),
    ('01KKZ3WGJSZF0F96SMYC000AVJ', 'wa_pdc',     'WA Public Disclosure Commission'),
    ('01KKZ3WGJSZF0F96SMYC000AVK', 'sec_form_d', 'SEC Form D'),
    ('01KKZ3WGJSZF0F96SMYC000AVM', 'wikipedia',  'Wikipedia'),
    ('01KKZ3WGJSZF0F96SMYC000AVN', 'other',      'Other')
ON CONFLICT (slug) DO NOTHING;

-- google_drive added separately to avoid regenerating existing seed IDs
INSERT INTO url_types (id, slug, display_name) VALUES
    ('01KM0YSNEMMPY35FSS3CX49SFJ', 'google_drive', 'Google Drive')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name) VALUES
    ('01KKZ3WGJSZF0F96SMYC000AVP', 'organization',    'org_ubi',       'UBI',    'Washington Unified Business Identifier'),
    ('01KKZ3WGJSZF0F96SMYC000AVQ', 'organization',    'org_wslcb',     'WSLCB',  'WA State Liquor and Cannabis Board License'),
    ('01KKZ3WGJSZF0F96SMYC000AVR', 'organization',    'org_wa_pdc',    'WA PDC', 'Washington State Public Disclosure Commission'),
    ('01KKZ3WGJSZF0F96SMYC000AVS', 'person',          'person_wa_pdc', 'WA PDC', 'Washington State Public Disclosure Commission'),
    ('01KKZ3WGJSZF0F96SMYC000AVT', 'person',          'person_ssn',    'SSN',    'United States Social Security Number'),
    ('01KKZ3WGJSZF0F96SMYC000AVV', 'role_assignment', 'role_wa_pdc',   'WA PDC', 'Washington State Public Disclosure Commission')
ON CONFLICT (slug) DO NOTHING;

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
