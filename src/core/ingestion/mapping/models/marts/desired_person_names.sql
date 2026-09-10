-- One legal name per desired person. PM keeps the canonical pointer, locale,
-- script, visibility and every non-legal name; the producer owns this row.
--
-- The overlay wins by presence, not by non-null: a curator who set the value
-- to null is asserting the producer's name should not stand, so the row
-- drops out (name is not null) rather than falling back to the mapped value.
with overlay as (
    select entity_id, value
    from {{ ref('stg_pm__curation_overlay') }}
    where entity_type = 'person' and field = 'name'
)

select
    d.pm_id,
    d.producer_id,
    case when o.entity_id is not null then o.value else i.name_full end as name,
    'legal' as name_type
from {{ ref('desired_people') }} as d
inner join {{ ref('int_person_identity') }} as i on i.producer_id = d.producer_id
left join overlay as o on o.entity_id = d.pm_id
where case when o.entity_id is not null then o.value else i.name_full end is not null
