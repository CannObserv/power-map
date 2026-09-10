-- The manifest keys desired_entity_events on (producer_id, event_type) — CR 28. A
-- duplicate of that tuple is a model defect, so this stays at error severity;
-- a single-column unique on producer_id would instead halt the build the day
-- a second owned event_type legitimately lands.
select
    producer_id,
    event_type,
    count(*) as n
from {{ ref('desired_entity_events') }}
group by producer_id, event_type
having count(*) > 1
