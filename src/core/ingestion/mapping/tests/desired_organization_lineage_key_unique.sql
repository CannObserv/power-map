-- The manifest keys desired_organization_lineage on (producer_id, event_type,
-- linked_pm_id) — #592: usa-wa's own key, with the linked end as the PM column
-- the applier matches on. A duplicate is a defect — or two producer orgs PM
-- holds as one, where no single link can be chosen — so error severity.
select
    producer_id,
    event_type,
    linked_pm_id,
    count(*) as n
from {{ ref('desired_organization_lineage') }}
group by producer_id, event_type, linked_pm_id
having count(*) > 1
