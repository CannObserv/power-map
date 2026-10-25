{{ config(severity='warn') }}
-- #671: a published period the capacity mart cannot claim, because its role is
-- not in desired_roles — a role_key no published role carries, a role PM would
-- refuse to create (role_create_pm_would_refuse), or one whose anchor left
-- scope. Silence would leave the seat judged by its type default without a word.
select
    p.role_key,
    p.valid_from,
    r.entity_id as role_producer_id
from {{ ref('stg_usa_wa__role_periods') }} as p
left join {{ ref('stg_usa_wa__roles') }} as r on r.role_key = p.role_key
left join {{ ref('desired_roles') }} as d on d.producer_id = r.entity_id
where d.producer_id is null
