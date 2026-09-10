-- Row-scoped: a row is the producer's claim about an org's parent, an absent
-- row is silence. That is what keeps the four PM-curated subcommittee parents
-- `agency` cannot express from being clobbered. The overlay wins by presence;
-- a null override withdraws the claim entirely.
with overlay as (
    select entity_id, value
    from {{ ref('stg_pm__curation_overlay') }}
    where entity_type = 'organization' and field = 'parent_id'
),

mapped as (
    select
        d.pm_id,
        d.producer_id,
        parent.pm_id as parent_pm_id
    from {{ ref('desired_organizations') }} as d
    inner join {{ ref('int_org_identity') }} as i on i.producer_id = d.producer_id
    inner join {{ ref('int_org_identity') }} as parent
        on parent.producer_id = i.parent_producer_id
)

select
    m.pm_id,
    case when o.entity_id is not null then o.value else m.parent_pm_id end as parent_pm_id,
    m.producer_id
from mapped as m
left join overlay as o on o.entity_id = m.pm_id
where case when o.entity_id is not null then o.value else m.parent_pm_id end is not null
