-- Identity only: the person exists and this producer id names it. usa-wa
-- fills no column on `people`; pronouns and notes stay PM's.
--
-- Row scope: a producer person with no crosswalk row is a create (pm_id null);
-- one whose anchor resolved live or merged is in scope; anything else —
-- archived (the #481 hazard), or unresolvable — is out, and is never minted
-- as a twin.
select
    pm_id,
    producer_id
from {{ ref('int_person_identity') }}
where
    not is_tombstone
    and (resolution is null or resolution in ('live', 'merged'))
