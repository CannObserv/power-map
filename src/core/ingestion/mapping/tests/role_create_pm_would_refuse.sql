{{ config(severity='warn') }}
-- A role usa-wa publishes that PM has no row for and `desired_roles` therefore
-- dropped: a create PM's own guards would refuse — no title, a district without
-- a type, a missing or stray position (#273/#302), or a position without a
-- district. The rule itself lives in `desired_roles`, which is the model that
-- acts on it; this names what that model left out, so the two cannot drift
-- apart. A role out of the producer's row scope is not a create at all, and an
-- anchored role is never dropped — absence is what archives it — so neither is
-- named here.
with pm as (
    select producer_id, pm_id, resolution
    from {{ ref('stg_pm__producer_crosswalk') }}
    where kind = 'role'
)

select
    r.entity_id,
    r.role_type,
    r.qualifier,
    r.district
from {{ ref('stg_usa_wa__roles') }} as r
left join pm on pm.producer_id = r.entity_id
left join {{ ref('desired_roles') }} as d on d.producer_id = r.entity_id
where
    pm.pm_id is null
    and (pm.resolution is null or pm.resolution in ('live', 'merged'))
    and d.producer_id is null
