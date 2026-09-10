-- One row per organization usa-wa publishes. all_varchar gives '' for absent;
-- nullif restores the distinction the models depend on (no long_name, no
-- acronym, no biennium).
select
    entity_id,
    name,
    nullif(long_name, '') as long_name,
    nullif(acronym, '') as acronym,
    nullif(agency, '') as agency,
    org_type,
    nullif(first_biennium, '') as first_biennium,
    nullif(last_biennium, '') as last_biennium
from {{ source('usa_wa', 'organizations') }}
