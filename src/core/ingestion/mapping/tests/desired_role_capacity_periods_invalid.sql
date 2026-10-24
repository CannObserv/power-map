-- CR 2 (#671): a period role_capacity_periods would refuse — its CHECKs
-- chk_capacity_period_range and chk_capacity_period_max, mirrored. Planned
-- clean, it would abort the supervised execute; error severity halts the build.
select
    producer_id,
    valid_from,
    valid_to,
    max_holders
from {{ ref('desired_role_capacity_periods') }}
where valid_to < valid_from or max_holders <= 0
