-- One row per capacity period usa-wa publishes (#671), keyed (role_key,
-- valid_from). `valid_to` is the period's last day, INCLUSIVE, and null when
-- open; `max_holders` is null for no limit. The basis (`kind` / `roster`) is
-- usa-wa's provenance; PM keeps no column for it.
--
-- max_holders is cast strictly: a value that is not a number fails the build.
-- Read as null it would mean "no limit" and hide every overlap on the seat.
select
    nullif(trim(role_key), '') as role_key,
    try_cast(nullif(trim(valid_from), '') as date) as valid_from,
    try_cast(nullif(trim(valid_to), '') as date) as valid_to,
    cast(nullif(trim(max_holders), '') as integer) as max_holders
from {{ source('usa_wa', 'role_periods') }}
