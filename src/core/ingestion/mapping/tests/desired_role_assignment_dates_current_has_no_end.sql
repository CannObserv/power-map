-- chk_current_no_end_date, before the applier plans an UPDATE (#527). The
-- producer's rule cannot break it (the end decides both), and the admin never
-- pins a contradicting pair — so a row here is a model or pin defect, and the
-- build stops rather than hand the applier a write the database would refuse.
select
    producer_id,
    pm_id,
    end_date
from {{ ref('desired_role_assignment_dates') }}
where is_current and end_date is not null
