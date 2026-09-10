-- One row per usa-wa person entity, resolved to the PM row it names.
--
-- Two crosswalks meet here and they are not the same thing:
--   * usa-wa's person_crosswalk carries merged_into tombstones — an entity
--     merged away *upstream*. Its loser is absent from persons
--     (retraction-as-absence), so the tombstone is the only signal that its PM
--     row should now point at the survivor (design § gap E).
--   * PM's producer_crosswalk says which PM row each producer id names, and
--     whether that resolution is in scope.
--
-- Chains are followed to the final survivor: a tombstone that points at
-- another tombstone would otherwise re-point PM at a row that is itself gone.
-- Bounded at 10 hops so a cycle in the producer's data terminates.
with recursive

tombstones as (
    select distinct entity_id, merged_into
    from {{ ref('stg_usa_wa__person_crosswalk') }}
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

persons as (
    select entity_id, name_full
    from {{ ref('stg_usa_wa__persons') }}
),

-- Every entity the producer speaks of: published persons, plus tombstoned
-- losers that are no longer published but still name a PM row.
entities as (
    select entity_id, name_full from persons
    union all
    select r.loser_id, null
    from resolved as r
    where r.loser_id not in (select entity_id from persons)
),

pm as (
    select producer_id, pm_id, resolution
    from {{ ref('stg_pm__producer_crosswalk') }}
    where kind = 'person'
)

select
    e.entity_id as producer_id,
    coalesce(r.survivor_id, e.entity_id) as survivor_producer_id,
    r.loser_id is not null as is_tombstone,
    e.name_full,
    own.pm_id,
    own.resolution,
    survivor.pm_id as survivor_pm_id
from entities as e
left join resolved as r on r.loser_id = e.entity_id
left join pm as own on own.producer_id = e.entity_id
left join pm as survivor on survivor.producer_id = coalesce(r.survivor_id, e.entity_id)
