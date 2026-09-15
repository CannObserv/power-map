-- The roles usa-wa publishes, read here only to map an assignment's role_key to
-- the role's entity id (#527). The roles model itself is #500's PR B.
select
    entity_id,
    nullif(trim(role_key), '') as role_key
from {{ source('usa_wa', 'roles') }}
