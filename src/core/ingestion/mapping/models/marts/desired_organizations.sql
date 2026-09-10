-- Identity only, same scope rule as desired_people: no crosswalk row is a
-- create; live or merged is in scope; archived or unresolvable is out.
select
    pm_id,
    producer_id
from {{ ref('int_org_identity') }}
where
    not is_tombstone
    and (resolution is null or resolution in ('live', 'merged'))
