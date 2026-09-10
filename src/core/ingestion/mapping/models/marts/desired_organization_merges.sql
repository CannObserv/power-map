-- Gap E for organizations: PM rows whose producer entity was merged away
-- upstream, and the survivor's PM row. Zero live today; the path is tested.
-- Scoped at both ends exactly as desired_person_merges is (CR 14).
with losers as (
    select producer_id, pm_id, survivor_producer_id
    from {{ ref('int_org_identity') }}
    where
        is_tombstone
        and pm_id is not null
        and resolution in ('live', 'merged')
)

select
    l.pm_id as loser_pm_id,
    s.pm_id as survivor_pm_id,
    l.producer_id as loser_producer_id,
    l.survivor_producer_id
from losers as l
left join {{ ref('desired_organizations') }} as s on s.producer_id = l.survivor_producer_id
where l.pm_id is distinct from s.pm_id
