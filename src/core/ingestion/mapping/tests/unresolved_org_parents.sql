{{ config(severity='warn') }}
-- CR 2: a parent the producer determines (agency House/Senate/Joint, or a
-- chamber's Legislature) that PM cannot resolve to an in-scope row. The
-- parents mart emits no claim for it — correct — but silence would hide an
-- unanchored chamber, or a parent whose anchor is archived. One row per case.
--
-- CR 17: the same silence would hide the anchor itself going missing — no
-- usa_wa_house / usa_wa_senate key in the producer's crosswalk, no org typed
-- legislature — which takes every dependent claim with it. That is the row
-- whose parent_rule names an anchor but whose parent_producer_id is null.
--
-- CR 27: the rule is read from int_org_identity.parent_rule, never restated
-- here; and only an in-scope child is named — an archived one claims nothing
-- whatever its parent resolves to.
select
    i.producer_id,
    i.parent_rule,
    i.parent_producer_id,
    p.pm_id as parent_pm_id,
    p.resolution as parent_resolution
from {{ ref('int_org_identity') }} as i
left join {{ ref('int_org_identity') }} as p on p.producer_id = i.parent_producer_id
where
    not i.is_tombstone
    and (i.resolution is null or i.resolution in ('live', 'merged'))
    and i.parent_rule is not null
    and (
        i.parent_producer_id is null
        or p.producer_id is null
        or p.pm_id is null
        or p.resolution not in ('live', 'merged')
    )
