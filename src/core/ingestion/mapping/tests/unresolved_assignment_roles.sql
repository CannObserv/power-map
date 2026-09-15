{{ config(severity='warn') }}
-- A span whose role_key names no published role (#527). Kept in
-- desired_role_assignments — dropping it would archive a live tenure — but with
-- no role the applier cannot create it, so it is named here.
select
    producer_id,
    pm_id
from {{ ref('desired_role_assignments') }}
where role_producer_id is null
