{{ config(severity='warn') }}
-- A pinned value its slot cannot read (#498). organization.dissolved_year is a
-- year: a value that does not cast to an integer is skipped by
-- desired_entity_events, so the producer's year stands — and this names the row,
-- because an override is never silently ignored (docs/SCHEMA.md § Curation
-- overlay). A null value is not malformed: it withdraws the dissolution.
select
    entity_type,
    entity_id,
    field,
    value
from {{ ref('stg_pm__curation_overlay') }}
where
    entity_type = 'organization'
    and field = 'dissolved_year'
    and value is not null
    and try_cast(value as integer) is null
