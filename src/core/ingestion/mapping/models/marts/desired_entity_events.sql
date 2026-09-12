-- dissolved is the only event type this model owns (design § events).
--
-- Year: a biennium '2019-20' ends in 2020 — first year + 1, which also reads
-- '1999-00' as 2000. Measured before this was written: all 152 of PM's
-- dissolved events on anchored orgs match this rule.
--
-- Current: the dataset's newest last_biennium, not the clock. The data reaches
-- the present, so an org whose last biennium is the newest one is live, and a
-- publish that lags a biennium under-claims rather than dissolving the living.
--
-- founded is never asserted: 35 orgs share the 1991-92 left edge, which is
-- where the records begin. Month and day are not asserted either — PM holds
-- finer precision on five of these, and a year-only claim must not erase it.
--
-- The overlay wins by presence (#498, organization.dissolved_year), as on the
-- other four slots: a pinned year dissolves the org in that year — even one the
-- producer holds live — and a null pin withdraws the dissolution. A value that
-- is not an integer is named by overlay_value_malformed and applied nowhere, so
-- it never reads as a withdrawal.
with current_biennium as (
    select max(last_biennium) as value
    from {{ ref('int_org_identity') }}
    where last_biennium is not null
),

produced as (
    select
        d.producer_id,
        cast(substr(i.last_biennium, 1, 4) as integer) + 1 as event_year
    from {{ ref('desired_organizations') }} as d
    inner join {{ ref('int_org_identity') }} as i on i.producer_id = d.producer_id
    cross join current_biennium as c
    where
        i.last_biennium is not null
        and i.last_biennium < c.value
),

overlay as (
    select
        entity_id,
        try_cast(value as integer) as event_year
    from {{ ref('stg_pm__curation_overlay') }}
    where
        entity_type = 'organization'
        and field = 'dissolved_year'
        and (value is null or try_cast(value as integer) is not null)
)

select
    d.pm_id,
    d.producer_id,
    'organization' as entity_type,
    'dissolved' as event_type,
    case when o.entity_id is not null then o.event_year else p.event_year end as event_year
from {{ ref('desired_organizations') }} as d
left join produced as p on p.producer_id = d.producer_id
left join overlay as o on o.entity_id = d.pm_id
where case when o.entity_id is not null then o.event_year else p.event_year end is not null
