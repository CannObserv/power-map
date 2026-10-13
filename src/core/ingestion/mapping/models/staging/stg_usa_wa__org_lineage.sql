-- One row per link usa-wa publishes (#592): the subject org, the link type, the
-- linked org, the year it took effect. The evidence URL, notes and WSL ids are
-- usa-wa's provenance; PM's link row has no column for them.
--
-- A slug outside the three PM maps fails the build (schema.yml): a new link type
-- is a contract change to map, never a row to drop quietly.
select
    trim(subject_entity_id) as subject_entity_id,
    trim(slug) as slug,
    trim(linked_entity_id) as linked_entity_id,
    try_cast(nullif(trim(effective_year), '') as integer) as effective_year
from {{ source('usa_wa', 'org_lineage') }}
