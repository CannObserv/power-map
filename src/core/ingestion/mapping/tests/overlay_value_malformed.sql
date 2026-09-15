{{ config(severity='warn') }}
-- A pinned value its slot cannot read (#498). organization.dissolved_year is a
-- year: a value that does not cast to an integer is skipped by
-- desired_entity_events, so the producer's year stands. An assignment's dates and
-- currency (#527) are skipped the same way by desired_role_assignment_dates. This
-- names the row, because an override is never silently ignored (docs/SCHEMA.md §
-- Curation overlay). A null value is not malformed: it withdraws the claim.
select
    entity_type,
    entity_id,
    field,
    value
from {{ ref('stg_pm__curation_overlay') }}
where
    value is not null
    and (
        (
            entity_type = 'organization'
            and field = 'dissolved_year'
            and try_cast(value as integer) is null
        )
        -- #527: an assignment's dates are dates, and is_current is what str(bool) wrote
        or (
            entity_type = 'assignment'
            and (
                (field in ('start_date', 'end_date') and try_cast(value as date) is null)
                or (field = 'is_current' and try_cast(value as boolean) is null)
            )
        )
    )
