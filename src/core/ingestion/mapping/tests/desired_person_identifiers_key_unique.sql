-- The manifest keys desired_person_identifiers on (producer_id, identifier_type,
-- value) — #710. A person carries several keys of one type (a returning
-- legislator), so no single column is unique; a duplicate tuple is a model defect.
select
    producer_id,
    identifier_type,
    value,
    count(*) as n
from {{ ref('desired_person_identifiers') }}
group by producer_id, identifier_type, value
having count(*) > 1
