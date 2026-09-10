{{ config(severity='warn') }}
-- The overlay's vocabulary, per entity type (CR 15). A row naming a field no
-- model maps for its entity type — or any row for an entity type no model
-- reads yet (role, assignment: #500) — is named here and applied nowhere: the
-- design's contract is that an override is never silently ignored
-- (docs/SCHEMA.md § Curation overlay). Mapping a new field means adding its
-- pair here and the model that reads it; #498's UI offers exactly these pairs.
--
-- Warn, not error (CR 26): the row was never going to be applied either way,
-- and at error severity one such row skipped every names/parents/acronyms
-- mart and wrote no desired state at all — the rule CR 16 set for a producer
-- row holds for a curator's.
select
    entity_type,
    entity_id,
    field
from {{ ref('stg_pm__curation_overlay') }}
where not (
    (entity_type = 'person' and field = 'name')
    or (entity_type = 'organization' and field in ('parent_id', 'legal_name', 'acronym'))
)
