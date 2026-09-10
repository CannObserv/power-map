-- One row per person usa-wa publishes. Verbatim except for one guard: a
-- name that is empty or whitespace becomes null. Measured on the real
-- snapshot: five anchored legislators arrive with name_full of ' ' or '', and
-- a space is not null — without this the model would assert it as a name.
select
    entity_id,
    nullif(trim(name_full), '') as name_full,
    nullif(trim(name_source), '') as name_source
from {{ source('usa_wa', 'persons') }}
