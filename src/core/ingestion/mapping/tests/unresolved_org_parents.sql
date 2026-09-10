{{ config(severity='warn') }}
-- CR 2: a parent the producer determines (agency House/Senate/Joint, or a
-- chamber's Legislature) that PM cannot resolve to an in-scope row. The
-- parents mart emits no claim for it — correct — but silence would hide an
-- unanchored chamber, or a parent whose anchor is archived. One row per case.
select
    i.producer_id,
    i.parent_producer_id,
    p.pm_id as parent_pm_id,
    p.resolution as parent_resolution
from {{ ref('int_org_identity') }} as i
left join {{ ref('int_org_identity') }} as p on p.producer_id = i.parent_producer_id
where
    i.parent_producer_id is not null
    and not i.is_tombstone
    and (p.producer_id is null or p.pm_id is null or p.resolution not in ('live', 'merged'))
