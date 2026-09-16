{{ config(severity='warn') }}
-- A role usa-wa publishes that PM has no row for and would refuse to create: no
-- title, a district without a type, a missing or stray position (#273/#302), or
-- a position without a district. desired_roles drops it — the trigger or CHECK
-- fires mid-transaction, and a refused INSERT would roll the whole run back — so
-- it is named here instead, and the role waits for the producer to fix it.
with pm as (
    select producer_id, pm_id from {{ ref('stg_pm__producer_crosswalk') }} where kind = 'role'
)

select
    r.entity_id,
    r.role_type,
    r.qualifier,
    r.district
from {{ ref('stg_usa_wa__roles') }} as r
left join pm on pm.producer_id = r.entity_id
left join {{ ref('stg_pm__role_types') }} as t on t.slug = r.role_type
where
    pm.pm_id is null
    and (
        r.name is null
        or (r.district is not null and r.role_type is null)
        or (r.district is not null and r.qualifier is null and coalesce(t.requires_qualifier, false))
        or (r.qualifier is not null and coalesce(t.forbids_qualifier, false))
        or (r.qualifier is not null and r.district is null)
    )
