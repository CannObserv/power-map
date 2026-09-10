-- One row per organization usa-wa publishes. all_varchar gives '' for absent;
-- trim + nullif restores the distinction the models depend on (no long_name,
-- no acronym, no biennium) and guards the whitespace case persons taught us.
--
-- A biennium is kept only when it is YYYY-YY (CR 18). Anything else becomes
-- null — no event is claimed for it — and tests/malformed_bienniums.sql names
-- it. Two failure modes this closes: a value the events model cannot cast
-- errors the build; and because "current" is max(last_biennium) as text, one
-- stray high value ('unknown', '9999') would out-sort every real biennium and
-- dissolve every live org.
select
    entity_id,
    nullif(trim(name), '') as name,
    nullif(trim(long_name), '') as long_name,
    nullif(trim(acronym), '') as acronym,
    nullif(trim(agency), '') as agency,
    org_type,
    case
        when regexp_matches(trim(first_biennium), '^\d{4}-\d{2}$') then trim(first_biennium)
    end as first_biennium,
    case
        when regexp_matches(trim(last_biennium), '^\d{4}-\d{2}$') then trim(last_biennium)
    end as last_biennium
from {{ source('usa_wa', 'organizations') }}
