-- One row per role usa-wa publishes (#527, #529), identified by its entity_id.
-- all_varchar gives '' for absent; nullif restores it, and a blank qualifier is
-- absence to PM's own guard too (enforce_role_forbids_qualifier btrims).
select
    entity_id,
    nullif(trim(role_key), '') as role_key,
    nullif(trim(role_type), '') as role_type,
    nullif(trim(name), '') as name,
    nullif(trim(span_kind), '') as span_kind,
    nullif(trim(org_entity_id), '') as org_entity_id,
    nullif(trim(district), '') as district,
    nullif(trim(qualifier), '') as qualifier
from {{ source('usa_wa', 'roles') }}
