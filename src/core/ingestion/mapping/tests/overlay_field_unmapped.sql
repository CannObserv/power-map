-- The overlay's vocabulary, per entity type (CR 15). A row naming a field no
-- model maps for its entity type — or any row for an entity type no model
-- reads yet (role, assignment: #500) — fails the build: the design's contract
-- is that an override is never silently ignored (docs/SCHEMA.md § Curation
-- overlay). Mapping a new field means adding its pair here and the model that
-- reads it; #498's UI offers exactly these pairs.
select
    entity_type,
    entity_id,
    field
from {{ ref('stg_pm__curation_overlay') }}
where not (
    (entity_type = 'person' and field = 'name')
    or (entity_type = 'organization' and field in ('parent_id', 'legal_name', 'acronym'))
)
