-- Whether a span's holder holds the role in their own right (#671): usa-wa's
-- `membership_type`, keyed like the dates. Only `principal` counts toward a
-- seat's capacity in the occupancy audit; a substitute serves under one.
--
-- A pin wins by presence (#498). Its value must be one of the vocabulary;
-- anything else is named by overlay_value_malformed and the producer's value
-- stands. A null pin withdraws the claim: the column is NOT NULL, so null is
-- never a value to write, and the row drops out.
with pins as (
    select
        entity_id,
        value
    from {{ ref('stg_pm__curation_overlay') }}
    where
        entity_type = 'assignment'
        and field = 'membership_type'
        and (value is null or value in ('principal', 'substitute', 'ex_officio', 'acting'))
)

select
    d.pm_id,
    d.producer_id,
    case when p.entity_id is not null then p.value else s.membership_type end as membership_type
from {{ ref('desired_role_assignments') }} as d
inner join {{ ref('stg_usa_wa__assignments') }} as s on s.span_key = d.producer_id
left join pins as p on p.entity_id = d.pm_id
where p.entity_id is null or p.value is not null
