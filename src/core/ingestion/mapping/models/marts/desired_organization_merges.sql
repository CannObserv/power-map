-- Gap E for organizations: PM rows whose producer entity was merged away
-- upstream, and the survivor's PM row. Zero live today; the path is tested.
select
    pm_id as loser_pm_id,
    survivor_pm_id,
    producer_id as loser_producer_id,
    survivor_producer_id
from {{ ref('int_org_identity') }}
where is_tombstone and pm_id is not null
