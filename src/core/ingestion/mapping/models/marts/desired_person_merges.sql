-- Gap E's output: PM rows whose producer entity was merged away upstream, and
-- the PM row they should now point at. #499 reads this to move assignments;
-- #500 reads it to retract the loser.
--
-- Both ends are scoped (CR 14). A merge instruction is a write, so the loser
-- must be a row the applier may touch — anchored live or merged, never
-- archived — and the survivor is resolved through desired_people, so an
-- archived or unanchored survivor yields null: reported by the warn test,
-- never acted on. A loser whose PM row already is the survivor's (PM made this
-- merge before the producer published it) is nothing to re-point.
with losers as (
    select producer_id, pm_id, survivor_producer_id
    from {{ ref('int_person_identity') }}
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
left join {{ ref('desired_people') }} as s on s.producer_id = l.survivor_producer_id
where l.pm_id is distinct from s.pm_id
