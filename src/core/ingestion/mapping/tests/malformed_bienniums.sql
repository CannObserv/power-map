{{ config(severity='warn') }}
-- CR 18: a biennium that is not YYYY-YY. Staging has already nulled it, so no
-- event is claimed for the org; this names the row so the defect reaches the
-- producer instead of being absorbed. Read from the source, not staging,
-- because staging is where the value was dropped.
select
    entity_id,
    first_biennium,
    last_biennium
from {{ source('usa_wa', 'organizations') }}
where
    (trim(first_biennium) <> '' and not regexp_matches(trim(first_biennium), '^\d{4}-\d{2}$'))
    or (trim(last_biennium) <> '' and not regexp_matches(trim(last_biennium), '^\d{4}-\d{2}$'))
