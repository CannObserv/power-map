-- PM's seeded crosswalk (#495): producer id → PM id, with how it resolved.
-- The applier's row scope is resolution in ('live', 'merged') (docs/SCHEMA.md).
select
    id,
    source,
    kind,
    producer_id,
    exported_pm_id,
    pm_id,
    resolution,
    export_generated_at,
    export_sha256,
    created_at,
    updated_at
from {{ source('pm', 'producer_crosswalk') }}
