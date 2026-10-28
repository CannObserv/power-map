-- Each person's WA Legislature keys as PM identifiers (#710): presence only.
-- usa-wa's crosswalk carries them; PM's `identifiers` table is what
-- resolve_entity reads, so a person the applier creates is findable by key.
--
-- Two namespaces map; usa_wa_legislature (the member id again) and wa_pdc have
-- no PM person type and stay out.
--
-- A key lands on the person it names: its own entity while that is in scope
-- (a usa-wa tombstone PM has not merged yet keeps it, and the person merge
-- re-homes it), else the tombstone's survivor. A key whose holder is out of
-- scope — archived, unresolvable — is dropped, never written onto a twin.
with types as (
    select * from (
        values
        ('usa_wa_legislature_roster', 'person_wa_legislature_roster'),
        ('wa_legislature_member_id', 'person_wa_legislature_member_id')
    ) as t (key_namespace, identifier_type)
),

identity as (
    select * from {{ ref('int_person_identity') }}
),

-- desired_people's scope, plus a tombstone whose own anchor is still in scope.
in_scope as (
    select producer_id, pm_id
    from identity
    where resolution in ('live', 'merged') or (resolution is null and not is_tombstone)
),

keys as (
    select
        case
            when i.resolution in ('live', 'merged') then i.producer_id
            else i.survivor_producer_id
        end as holder_id,
        t.identifier_type,
        k.key_value as value
    from {{ ref('stg_usa_wa__person_crosswalk') }} as k
    inner join types as t on t.key_namespace = k.key_namespace
    inner join identity as i on i.producer_id = k.entity_id
)

select
    s.pm_id,
    s.producer_id,
    k.identifier_type,
    k.value
from keys as k
inner join in_scope as s on s.producer_id = k.holder_id
