-- One row per organization usa-wa publishes. all_varchar gives '' for absent;
-- trim + nullif restores the distinction the models depend on (no long_name,
-- no acronym, no biennium) and guards the whitespace case persons taught us.
select
    entity_id,
    nullif(trim(name), '') as name,
    nullif(trim(long_name), '') as long_name,
    nullif(trim(acronym), '') as acronym,
    nullif(trim(agency), '') as agency,
    org_type,
    nullif(first_biennium, '') as first_biennium,
    nullif(last_biennium, '') as last_biennium
from {{ source('usa_wa', 'organizations') }}
