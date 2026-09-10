-- Gap E's output: PM rows whose producer entity was merged away upstream, and
-- the PM row they should now point at. Only losers PM actually holds — a
-- tombstone for something PM never had is nothing to re-point. #499 reads
-- this to move assignments; #500 reads it to retract the loser.
select
    pm_id as loser_pm_id,
    survivor_pm_id,
    producer_id as loser_producer_id,
    survivor_producer_id
from {{ ref('int_person_identity') }}
where is_tombstone and pm_id is not null
