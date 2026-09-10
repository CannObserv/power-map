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
with current_biennium as (
    select max(last_biennium) as value
    from {{ ref('int_org_identity') }}
    where last_biennium is not null
)

select
    d.pm_id,
    d.producer_id,
    'organization' as entity_type,
    'dissolved' as event_type,
    cast(substr(i.last_biennium, 1, 4) as integer) + 1 as event_year
from {{ ref('desired_organizations') }} as d
inner join {{ ref('int_org_identity') }} as i on i.producer_id = d.producer_id
cross join current_biennium as c
where
    i.last_biennium is not null
    and i.last_biennium < c.value
