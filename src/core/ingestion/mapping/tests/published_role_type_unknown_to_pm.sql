{{ config(severity='warn', store_failures=true) }}
-- A role_type usa-wa publishes that PM's vocabulary does not carry (#883), one
-- row per slug. role_types has no remote write path (#302): a new type waits for
-- a deploy that seeds it. Until then the applier calls each create on it stale,
-- which already halts an execute, so this warns rather than halting the build —
-- an error here would skip every model and hide the rest of the diff. It is
-- also the only word on an anchored role whose type moved: identity is written
-- once, and the applier never reads it again. store_failures: build_desired_state
-- logs each row, so the journal names the slug, not only a count.
select
    r.role_type,
    count(*) as roles,
    min(r.role_key) as sample_role_key
from {{ ref('stg_usa_wa__roles') }} as r
left join {{ ref('stg_pm__role_types') }} as t on t.slug = r.role_type
where r.role_type is not null and t.slug is null
group by r.role_type
