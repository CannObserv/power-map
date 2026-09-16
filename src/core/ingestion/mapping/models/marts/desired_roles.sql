-- One row per published role (#529), keyed by usa-wa's role entity_id.
--
-- Row scope: a role with no crosswalk row is a create (pm_id null); one whose
-- anchor resolved live or merged is in scope; anything else — archived, or
-- unresolvable — is out, and is never minted as a twin (desired_people's rule).
--
-- Identity carries the producer's organization id and PM's own vocabulary slugs,
-- never PM ids: the applier resolves the organization through the live crosswalk
-- and each slug through the table the manifest names. A **create** PM's guards
-- would refuse — no title, a missing or stray position (#273/#302), a district
-- without a type — is dropped here and named by role_create_pm_would_refuse,
-- since those are triggers and a CHECK, which fire mid-transaction. An anchored
-- role is kept whatever it carries: dropping it would read as absence, and
-- absence archives.
with pm as (
    select producer_id, pm_id, resolution
    from {{ ref('stg_pm__producer_crosswalk') }}
    where kind = 'role'
),

published as (
    select
        pm.pm_id,
        r.entity_id as producer_id,
        r.org_entity_id as org_producer_id,
        r.role_type,
        case when r.district is not null then 'usa-wa-ld-' || r.district end as jurisdiction_slug,
        r.qualifier,
        r.name as title,
        coalesce(t.requires_qualifier, false) as requires_qualifier,
        coalesce(t.forbids_qualifier, false) as forbids_qualifier
    from {{ ref('stg_usa_wa__roles') }} as r
    left join pm on pm.producer_id = r.entity_id
    left join {{ ref('stg_pm__role_types') }} as t on t.slug = r.role_type
    where pm.resolution is null or pm.resolution in ('live', 'merged')
)

select
    pm_id,
    producer_id,
    org_producer_id,
    role_type,
    jurisdiction_slug,
    qualifier,
    title
from published
where
    pm_id is not null
    or not (
        title is null
        or (jurisdiction_slug is not null and role_type is null)
        or (jurisdiction_slug is not null and qualifier is null and requires_qualifier)
        or (qualifier is not null and forbids_qualifier)
        or (qualifier is not null and jurisdiction_slug is null)
    )
