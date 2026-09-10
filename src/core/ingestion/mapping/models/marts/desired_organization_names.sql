-- One legal name per desired org: long_name where the producer has one, else
-- name. No dba from the short name — PM never stored it (design revision
-- 2026-09-10). The overlay wins by presence; a null override drops the row.
with overlay as (
    select entity_id, value
    from {{ ref('stg_pm__curation_overlay') }}
    where entity_type = 'organization' and field = 'legal_name'
)

select
    d.pm_id,
    d.producer_id,
    case when o.entity_id is not null then o.value else coalesce(i.long_name, i.name) end as name,
    'legal' as name_type
from {{ ref('desired_organizations') }} as d
inner join {{ ref('int_org_identity') }} as i on i.producer_id = d.producer_id
left join overlay as o on o.entity_id = d.pm_id
where case when o.entity_id is not null then o.value else coalesce(i.long_name, i.name) end
    is not null
