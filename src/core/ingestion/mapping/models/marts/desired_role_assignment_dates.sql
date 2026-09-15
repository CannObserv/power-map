-- The three columns usa-wa owns on an assignment (#527), by the dataset's own
-- rule: is_active is exactly "no valid_to", so the end decides both. A null
-- end_date is a claim here, not silence — the manifest's asserts_null — which is
-- how a span usa-wa#289 collapsed reopens the PM row that ended it early.
--
-- Each pinned slot wins by presence (#498); a pin whose value does not cast is
-- named by overlay_value_malformed and applied nowhere. The pair stays legal
-- against chk_current_no_end_date: a pinned is_current = true clears the end,
-- and a pinned end date closes the span. Both pinned are taken as pinned, and
-- tests/desired_role_assignment_dates_current_has_no_end.sql refuses a
-- contradiction rather than let the applier plan an UPDATE the CHECK rejects.
with pins as (
    select
        entity_id,
        field,
        value
    from {{ ref('stg_pm__curation_overlay') }}
    where
        entity_type = 'assignment'
        and (
            value is null
            or (field in ('start_date', 'end_date') and try_cast(value as date) is not null)
            or (field = 'is_current' and try_cast(value as boolean) is not null)
        )
),

mapped as (
    select
        d.pm_id,
        d.producer_id,
        s.valid_from as start_date,
        s.valid_to as end_date,
        s.valid_to is null as is_current
    from {{ ref('desired_role_assignments') }} as d
    inner join {{ ref('stg_usa_wa__assignments') }} as s on s.span_key = d.producer_id
),

pinned as (
    select
        m.*,
        ps.entity_id is not null as start_pinned,
        try_cast(ps.value as date) as pin_start,
        pe.entity_id is not null as end_pinned,
        try_cast(pe.value as date) as pin_end,
        pc.entity_id is not null as current_pinned,
        try_cast(pc.value as boolean) as pin_current
    from mapped as m
    left join pins as ps on ps.entity_id = m.pm_id and ps.field = 'start_date'
    left join pins as pe on pe.entity_id = m.pm_id and pe.field = 'end_date'
    left join pins as pc on pc.entity_id = m.pm_id and pc.field = 'is_current'
)

select
    pm_id,
    producer_id,
    case when start_pinned then pin_start else start_date end as start_date,
    case
        when end_pinned then pin_end
        when current_pinned and pin_current then null
        else end_date
    end as end_date,
    case
        when current_pinned then pin_current
        when end_pinned and pin_end is not null then false
        else is_current
    end as is_current
from pinned
