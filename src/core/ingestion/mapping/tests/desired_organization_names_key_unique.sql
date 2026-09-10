-- The manifest keys desired_organization_names on (producer_id, name_type) — CR 28. A
-- duplicate of that tuple is a model defect, so this stays at error severity;
-- a single-column unique on producer_id would instead halt the build the day
-- a second owned name_type legitimately lands.
select
    producer_id,
    name_type,
    count(*) as n
from {{ ref('desired_organization_names') }}
group by producer_id, name_type
having count(*) > 1
