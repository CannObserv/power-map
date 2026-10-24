-- Seat capacity per role and period (#671): usa-wa's role_periods, claimed under
-- the role's PM id. Keyed (producer_id, valid_from) — the role's producer id and
-- the period's first day; valid_to (inclusive, null = open) and max_holders
-- (null = no limit) are owned, and a null in either is a value.
--
-- Row scope is desired_roles': a role this run creates carries no pm_id and the
-- applier inserts under the new row; a role PM would refuse, or one out of scope,
-- claims nothing, and tests/unresolved_role_capacity_periods.sql names it.
select
    d.pm_id,
    d.producer_id,
    p.valid_from,
    p.valid_to,
    p.max_holders
from {{ ref('stg_usa_wa__role_periods') }} as p
inner join {{ ref('stg_usa_wa__roles') }} as r on r.role_key = p.role_key
inner join {{ ref('desired_roles') }} as d on d.producer_id = r.entity_id
