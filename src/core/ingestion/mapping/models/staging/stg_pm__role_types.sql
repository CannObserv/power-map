-- PM's role-type vocabulary (#266), read only for the two qualifier policies its
-- triggers enforce (#273/#302): the models drop a create PM would refuse rather
-- than let the applier plan an INSERT the database raises on.
select
    id,
    slug,
    requires_qualifier,
    forbids_qualifier
from {{ source('pm', 'role_types') }}
