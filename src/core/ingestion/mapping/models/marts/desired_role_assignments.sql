-- One row per published span (#527), keyed by its span_key (#525).
--
-- Row scope: a span with no crosswalk row is a create (pm_id null); one whose
-- anchor resolved live or merged is in scope; anything else — archived, or
-- unresolvable — is out, and is never minted as a twin (desired_people's rule).
--
-- Identity carries producer ids, not PM ids: the applier resolves the person and
-- the role through the live crosswalk, or to the row the same run creates for
-- them. A span whose role_key names no published role is kept, with no role —
-- dropping it would read as absence and archive a live tenure — and is named by
-- tests/unresolved_assignment_roles.sql; the applier cannot create it.
with pm as (
    select producer_id, pm_id, resolution
    from {{ ref('stg_pm__producer_crosswalk') }}
    where kind = 'assignment'
)

select
    pm.pm_id,
    s.span_key as producer_id,
    s.entity_id as person_producer_id,
    r.entity_id as role_producer_id,
    s.valid_from as start_date
from {{ ref('stg_usa_wa__assignments') }} as s
left join {{ ref('stg_usa_wa__roles') }} as r on r.role_key = s.role_key
left join pm on pm.producer_id = s.span_key
where pm.resolution is null or pm.resolution in ('live', 'merged')
