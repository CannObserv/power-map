-- PM-wins overrides on producer-owned fields (#497). Presence of a row is the
-- override; value may be null to assert the field should be empty.
--
-- Active pins only (#498): unpin archives the row, which stays in the table as
-- history — an archived row here would keep overriding a value the curator let go.
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
where archived_at is null
