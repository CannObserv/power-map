-- One row per usa-wa organization entity, resolved to its PM row, with the
-- parent the producer determines (if any) and the columns the marts read.
--
-- The tombstone walk is the same as int_person_identity's. The parent rule is
-- the one measured against PM: agency House → the House chamber, Senate → the
-- Senate chamber, Joint → the Legislature, and a chamber → the Legislature.
-- 'Other' and no agency determine nothing — those orgs' parents (subcommittees
-- under committees, the Legislature itself, the parties) are PM's to curate,
-- so no row is a claim, not a claim of null.
--
-- The rule is stated once, as `parent_rule` (CR 27): the anchor CASE below
-- and tests/unresolved_org_parents.sql both read it, so a rule added here is
-- covered there without a second copy. Chamber ids come from the producer's
-- own crosswalk keys (usa_wa_house, usa_wa_senate), the Legislature from
-- org_type; nothing is hard-coded.
with recursive

tombstones as (
    select distinct entity_id, merged_into
    from {{ ref('stg_usa_wa__org_crosswalk') }}
    where merged_into is not null
),

walk as (
    select entity_id as loser_id, merged_into as survivor_id, 1 as depth
    from tombstones
    union all
    select w.loser_id, t.merged_into, w.depth + 1
    from walk as w
    inner join tombstones as t on t.entity_id = w.survivor_id
    where w.depth < 10
),

resolved as (
    select loser_id, survivor_id
    from (
        select
            loser_id,
            survivor_id,
            row_number() over (partition by loser_id order by depth desc) as rn
        from walk
    )
    where rn = 1
),

orgs as (
    select *
    from {{ ref('stg_usa_wa__organizations') }}
),

anchors as (
    select
        max(case when key_value = 'usa_wa_house' then entity_id end) as house_id,
        max(case when key_value = 'usa_wa_senate' then entity_id end) as senate_id
    from {{ ref('stg_usa_wa__org_crosswalk') }}
    where key_namespace = 'usa_wa_legislature'
),

legislature as (
    select max(entity_id) as legislature_id
    from orgs
    where org_type = 'legislature'
),

entities as (
    select
        entity_id,
        name,
        long_name,
        acronym,
        agency,
        org_type,
        first_biennium,
        last_biennium
    from orgs
    union all
    select
        r.loser_id,
        null, null, null, null, null, null, null
    from resolved as r
    where r.loser_id not in (select entity_id from orgs)
),

-- Which anchor, if any, an org's agency / org_type determines.
ruled as (
    select
        *,
        case
            when agency = 'House' then 'house'
            when agency = 'Senate' then 'senate'
            when agency = 'Joint' then 'legislature'
            when org_type = 'chamber' then 'legislature'
        end as parent_rule
    from entities
),

pm as (
    select producer_id, pm_id, resolution
    from {{ ref('stg_pm__producer_crosswalk') }}
    where kind = 'organization'
)

select
    e.entity_id as producer_id,
    coalesce(r.survivor_id, e.entity_id) as survivor_producer_id,
    r.loser_id is not null as is_tombstone,
    e.name,
    e.long_name,
    e.acronym,
    e.agency,
    e.org_type,
    e.first_biennium,
    e.last_biennium,
    e.parent_rule,
    case e.parent_rule
        when 'house' then a.house_id
        when 'senate' then a.senate_id
        when 'legislature' then l.legislature_id
    end as parent_producer_id,
    own.pm_id,
    own.resolution,
    survivor.pm_id as survivor_pm_id
from ruled as e
cross join anchors as a
cross join legislature as l
left join resolved as r on r.loser_id = e.entity_id
left join pm as own on own.producer_id = e.entity_id
left join pm as survivor on survivor.producer_id = coalesce(r.survivor_id, e.entity_id)
