{{ config(severity='warn') }}
-- CR 2: a parent the producer determines (agency House/Senate/Joint, or a
-- chamber's Legislature) that PM cannot resolve to an in-scope row. The
-- parents mart emits no claim for it — correct — but silence would hide an
-- unanchored chamber, or a parent whose anchor is archived. One row per case.
--
-- CR 17: the same silence would hide the anchor itself going missing — no
-- usa_wa_house / usa_wa_senate key in the producer's crosswalk, no org typed
-- legislature — which takes every dependent claim with it. A row whose agency
-- or org_type determines a parent but whose parent_producer_id is null is that
-- case; parent_pm_id and parent_resolution are null on it too.
select
    i.producer_id,
    i.parent_producer_id,
    p.pm_id as parent_pm_id,
    p.resolution as parent_resolution
from {{ ref('int_org_identity') }} as i
left join {{ ref('int_org_identity') }} as p on p.producer_id = i.parent_producer_id
where
    not i.is_tombstone
    and (
        (
            i.parent_producer_id is not null
            and (p.producer_id is null or p.pm_id is null or p.resolution not in ('live', 'merged'))
        )
        or (
            i.parent_producer_id is null
            and (i.agency in ('House', 'Senate', 'Joint') or i.org_type = 'chamber')
        )
    )
