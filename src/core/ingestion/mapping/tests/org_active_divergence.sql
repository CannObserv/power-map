{{ config(severity='warn') }}
-- #592: PM keeps organizations.active; this names the anchored, unarchived orgs
-- where usa-wa's flag disagrees, for a curator to judge (set_org_active).
--
-- Only orgs with no last_biennium — Joint/Other, parties, chambers. A committee's
-- life is the dissolved event's, and usa-wa flips every committee inactive at a
-- biennium rollover: hinting on them would warn on the whole roster for a night.
-- The class this does read is where GFIE (#591) drifted unseen.
select
    d.producer_id,
    d.pm_id,
    s.org_type,
    s.active as producer_active,
    o.active as pm_active
from {{ ref('desired_organizations') }} as d
inner join {{ ref('stg_usa_wa__organizations') }} as s on s.entity_id = d.producer_id
inner join {{ ref('stg_pm__organizations') }} as o on o.id = d.pm_id
where
    o.archived_at is null
    and s.last_biennium is null
    and s.active is not null
    and s.active is distinct from o.active
