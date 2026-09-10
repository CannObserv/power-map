-- One acronym per desired org that publishes one. Overlay by presence.
with overlay as (
    select entity_id, value
    from {{ ref('stg_pm__curation_overlay') }}
    where entity_type = 'organization' and field = 'acronym'
)

select
    d.pm_id,
    d.producer_id,
    case when o.entity_id is not null then o.value else i.acronym end as acronym
from {{ ref('desired_organizations') }} as d
inner join {{ ref('int_org_identity') }} as i on i.producer_id = d.producer_id
left join overlay as o on o.entity_id = d.pm_id
where case when o.entity_id is not null then o.value else i.acronym end is not null
