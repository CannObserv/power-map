-- PM's own organizations.active (#592): the applier never writes it. Read only by
-- tests/org_active_divergence.sql, which names an org usa-wa's flag disagrees on.
select
    id,
    active,
    archived_at
from {{ source('pm', 'organizations') }}
