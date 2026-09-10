-- usa-wa's own crosswalk: one row per (entity, source key). A row whose
-- merged_into is set is a tombstone — the entity was merged away upstream and
-- this is the only signal that says where it went (design § gap E). The CSV
-- carries '' for absent, hence nullif.
select
    entity_id,
    natural_key,
    key_namespace,
    key_value,
    registered_by,
    nullif(merged_into, '') as merged_into
from {{ source('usa_wa', 'person_crosswalk') }}
