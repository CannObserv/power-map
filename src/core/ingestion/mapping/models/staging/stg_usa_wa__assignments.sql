-- One row per span usa-wa publishes (#527), identified by its span_key (#525).
-- all_varchar gives '' for absent; nullif restores it. A date that does not cast
-- is null rather than an error, and a missing start is named in schema.yml.
select
    nullif(trim(span_key), '') as span_key,
    entity_id,
    nullif(trim(role_key), '') as role_key,
    try_cast(nullif(trim(valid_from), '') as date) as valid_from,
    try_cast(nullif(trim(valid_to), '') as date) as valid_to
from {{ source('usa_wa', 'assignments') }}
