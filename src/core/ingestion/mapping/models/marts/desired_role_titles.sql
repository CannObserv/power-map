-- The one column usa-wa owns on a role (#529). A pin wins by presence (#498), so
-- a pinned null asserts nothing and PM's own title stands — the same rule every
-- column binding follows, where a null is silence rather than "clear it".
with pins as (
    select entity_id, value
    from {{ ref('stg_pm__curation_overlay') }}
    where entity_type = 'role' and field = 'title'
)

select
    d.pm_id,
    d.producer_id,
    case when p.entity_id is not null then p.value else d.title end as title
from {{ ref('desired_roles') }} as d
left join pins as p on p.entity_id = d.pm_id
