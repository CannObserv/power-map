-- usa-wa's org crosswalk; merged_into is the upstream tombstone (gap E).
select
    entity_id,
    natural_key,
    key_namespace,
    key_value,
    registered_by,
    nullif(merged_into, '') as merged_into
from {{ source('usa_wa', 'org_crosswalk') }}
