-- The manifest keys desired_role_capacity_periods on (producer_id, valid_from)
-- — usa-wa's own key, the role resolved (#671). Two periods on one start cannot
-- both land under the partial unique index, so error severity.
select
    producer_id,
    valid_from,
    count(*) as n
from {{ ref('desired_role_capacity_periods') }}
group by producer_id, valid_from
having count(*) > 1
