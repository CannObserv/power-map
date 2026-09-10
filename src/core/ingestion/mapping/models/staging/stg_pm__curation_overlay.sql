-- PM-wins overrides on producer-owned fields (#497). Presence of a row is the
-- override; value may be null to assert the field should be empty.
select
    id,
    entity_type,
    entity_id,
    field,
    value,
    note,
    created_by,
    created_at,
    updated_at
from {{ source('pm', 'curation_overlay') }}
