{{ config(severity='warn') }}
-- #592: a published link the lineage mart cannot claim, because an end has no
-- in-scope PM id — a create (it lands the night after the create executes), an
-- archived anchor, a usa-wa tombstone, or an id no dataset publishes. Silence
-- would hide every one of them. One row per link, naming the end that failed.
-- A link whose two ends resolve to one PM org (CR 1) is named here as well.
select
    l.subject_entity_id,
    l.slug,
    l.linked_entity_id,
    s.pm_id as subject_pm_id,
    t.pm_id as linked_pm_id
from {{ ref('stg_usa_wa__org_lineage') }} as l
left join {{ ref('desired_organizations') }} as s on s.producer_id = l.subject_entity_id
left join {{ ref('desired_organizations') }} as t on t.producer_id = l.linked_entity_id
where s.pm_id is null or t.pm_id is null or s.pm_id = t.pm_id
