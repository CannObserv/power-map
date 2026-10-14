-- The links PM owns (#592): one per published (subject, type, linked) whose two
-- ends are both in scope. The event lives on the subject and names the linked
-- org, as PM's own succession rows do (#469); only the year is claimed, so PM's
-- finer month and day survive, as on `dissolved`.
--
-- Both ends resolve through desired_organizations with a PM id. A create, an
-- archived anchor, a tombstone or an id nobody publishes drops the link here and
-- tests/unresolved_org_lineage.sql names it: an end that is a create lands the
-- night after its create executes. Out of scope at either end is out of scope —
-- a claim naming a row the applier must not touch is never emitted. A link whose
-- two ends are one PM org is refused too (CR 1): entity_events has no CHECK
-- against it, and a dated self-link would end the org's own lifespan.
--
-- No overlay: a link between two anchored orgs is locked, not pinned (#592 L2).
select
    s.pm_id,
    s.producer_id,
    l.slug as event_type,
    t.pm_id as linked_pm_id,
    t.producer_id as linked_producer_id,
    l.effective_year as event_year
from {{ ref('stg_usa_wa__org_lineage') }} as l
inner join {{ ref('desired_organizations') }} as s on s.producer_id = l.subject_entity_id
inner join {{ ref('desired_organizations') }} as t on t.producer_id = l.linked_entity_id
where s.pm_id is not null and t.pm_id is not null and s.pm_id <> t.pm_id
