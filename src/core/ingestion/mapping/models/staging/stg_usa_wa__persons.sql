-- One row per person usa-wa publishes. Verbatim: the only transformation at
-- this layer is typing, and every column here is text.
select
    entity_id,
    name_full,
    name_source
from {{ source('usa_wa', 'persons') }}
