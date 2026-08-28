# power-map — Recurring Data Audits

The six recurring integrity audits: what each one checks, the categories it
reports, and how to run it. Each is read-only in report mode; the ones that can
repair take `--execute`, and the resolver and target-echo rules are in
`docs/RUNBOOKS.md` §"Operational scripts — dry run by default & target echo".

Four run on a systemd timer — assignment-relationship windows (#301), per-key API
anomaly (#294), schema parity (#315/#331) and ancillary orphans (#324/#326/#319).
Those four exit 3 on findings, so a run surfaces in `systemctl --failed` (#363),
and each carries its own install block below. The org-lifecycle (#307) and
duplicate-assignment (#311) audits are on-demand: no timer, no exit-3.

The importer, the idempotent seeds and the TTL prune are in `docs/RUNBOOKS.md`;
incident triage for an unreachable database is in `docs/RUNBOOK_DB_TRIAGE.md`.

---

## Org-lifecycle assignment audit (issue #307)


`scripts/audit_org_lifecycle_assignments.py` checks every non-archived
assignment against its org's lifespan (`v_org_lifespan.ended_on`, derived from
`dissolved`/`merged_with` entity events — see `docs/OBSERVATIONS.md`
§ "Org lifespan bounds on assignments"). Categories:

- `current_on_ended` — auto-fixable; `--execute` closes at `ended_on`
  (`is_current=FALSE`, provenance note appended to `notes`)
- `end_after_ended` / `start_after_ended` — dated contradictions, report-only
- `unknown_end_on_ended` — unknown end left open, report-only
- `missing_end_event` — inactive/archived org with open assignments but no end
  event; record a `dissolved`/`merged_with` event in admin, then re-run

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_org_lifecycle_assignments            # report
uv run "${env_args[@]}" python -m scripts.audit_org_lifecycle_assignments --execute  # close
```

Idempotent — a compliant DB yields no findings and `--execute` is a no-op.

---

## Assignment-relationship window audit (issue #301)


`scripts/audit_assignment_relationship_windows.py` reconciles active
role-assignment relationship edges whose window has drifted outside the
intersection of both endpoint assignment windows (the observation path records
freely) — the steady-state counterpart to the `cascade_assignment_relationships`
trigger, sharing its exact clamp rule. Categories:

- `clamp` — `--execute` raises a defined `valid_from` up / lowers-or-materializes
  `valid_until` down to the endpoint intersection (unknown start never invented, #307)
- `inverted` — clamp inverts the window; `--execute` archives the edge
- `archived_endpoint` — an endpoint assignment is archived; `--execute` archives the edge

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_assignment_relationship_windows            # report
uv run "${env_args[@]}" python -m scripts.audit_assignment_relationship_windows --execute  # fix
```

Idempotent. Report mode **exits 3 when any drift is found** (0 when clean), so the
daily `power-map-assignment-rel-windows.timer` shows as failed in `systemctl --failed`
and can drive `OnFailure=` — same convention as the ancillary-orphans / schema-parity
audits (#363). `--execute` reconciles the drift and always exits 0.

---

## Duplicate-assignment audit (issue #311)


`scripts/audit_assignment_duplicates.py` finds overlapping active assignment
pairs for the same `(person, role)` — the duplicates minted when a producer's
start_date correction missed the match key pre-#311 (see `docs/API_ASSIGNMENTS.md`
§ "Write semantics & provenance"). Categories:

**Coverage is the merge gate (#476).** Both auto-merge categories require the
same proof — the orphan's end is dated *and* the survivor covers it (dated end
≥ the orphan's, or the survivor open with `is_current`). Creation order only
picks which auto-merge category a covering pair lands in:

- `deepened_start` — covering, wider (earlier-start) row created later: the
  producer-correction signature; auto-merged by `--execute`
- `subsumed` — covering, wider row created first; auto-merged
- `overlapping_review` — coverage unprovable (unknown end on the survivor, an
  open-ended orphan, or a survivor ending *before* its orphan), report-only

**Rule: this audit never invents a span, in either direction.** The merge keeps
the survivor's window as stored and reconciles no dates, so merging an unproven
pair discards the orphan's tenure outright — which is what `deepened_start` did
before #476 (#474: 21 archivals onto a survivor that ended first, 11 of the
orphans still open). Widening the survivor to the union was investigated and
**rejected**: 21 of those 22 survivors carry a producer-authored update, dated
after the audit ran, whose `end_date` is exactly what PM stores — real
departures, resignations and a death in office. Coverage the audit cannot prove
is a human decision; `overlapping_review` is where it belongs.

Merge = links/contact methods/addresses/identifiers move to the survivor
(would-be duplicates stay on the orphan), notes concatenate, orphan is
**archived** (never deleted) with a provenance note that names the survivor and
records the span the merge dropped — `Archived as duplicate of {id} (#311
audit). Span was {start}..{end}.` (`end` reads `open` when undated). The archive
UPDATE hits the `entity_changes` outbox so subscribed producers drop stale
anchors. Undated tenures and disjoint terms (returning legislators) are never
flagged.

The 21 pre-#476 archivals stay as they are — they match the producer's own
newest assertions; no data repair is in scope.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_assignment_duplicates            # report
uv run "${env_args[@]}" python -m scripts.audit_assignment_duplicates --execute  # merge
```

Idempotent — merged pairs leave the audit's scope (archived rows are ignored).

---

## Per-key API anomaly check (issue #294)


`scripts/check_api_anomalies.py` queries `api_request_log` for the trailing hour,
grouped per API key, and logs a journal `WARNING` for every key at/above the
threshold (default 5000/hr; env `API_ANOMALY_HOURLY_THRESHOLD`; `<= 0` disables).
Exits 3 when anomalous — distinct from argparse usage errors (exit 2) — so the
systemd unit shows failed (`systemctl --failed`; future
`OnFailure=` hook). The threshold is deliberately **below** the rate-limit
ceiling (2 workers × 2/s ≈ 14.4k/hr) — the 2026-07-11 runaway ran at ~17.5k/hr,
so a "well above ceiling" threshold would have missed it. Human-facing layer:
Admin → Activity → API Requests per-key panel.

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.check_api_anomalies
uv run "${env_args[@]}" python -m scripts.check_api_anomalies --threshold 1000
```

Scheduled (production): an hourly systemd timer. Install / update:

```bash
sudo cp infra/power-map-anomaly.service infra/power-map-anomaly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-anomaly.timer

# Inspect
systemctl list-timers power-map-anomaly.timer    # next/last run
sudo systemctl start power-map-anomaly.service   # run once, now
sudo journalctl -u power-map-anomaly -f          # WARNINGs per anomalous key
```

---

## Schema-parity audit (issues #315, #331)


`scripts/audit_schema_constraint_parity.py` snapshots every **constraint**
(`pg_get_constraintdef`), **function** (`pg_get_functiondef`), and **trigger**
(`pg_get_triggerdef`) on a reference DB (`--reference-url`, default
`PARITY_REFERENCE_URL` → `TEST_DATABASE_URL`) and on prod (`--target-url`,
default `DATABASE_URL`), and exits 3 when prod is missing or disagrees on any
reference object — the `CREATE TABLE IF NOT EXISTS` inline-constraint drift class
(#307/#312 CHECKs, #315's FK `ON DELETE` action) plus the `CREATE OR REPLACE`
function/trigger body-drift window (#331; the change-feed `touch_parent_*` /
`trg_touch_entity_*` surface). Compares the **full def**, not just presence, so
FK actions, CHECK bodies, and function/trigger bodies are in scope. The
per-kind report namespaces objects `constraint.*` / `function.*` / `trigger.*`.
Read-only; catches drift from any source (manual DDL, partial migration, a
hand-applied hotfix, a deploy whose `apply_schema` no-op'd a new inline
constraint).

**Expected drift while a schema branch is in flight.** The default reference is
`co_pm_db_test`, and the documented worktree loop applies a *branch's* schema to
it (`bash scripts/apply-schema.sh --test`). From that moment the reference is
ahead of prod and the timer exits 3 — correctly, on objects that do not exist in
production yet. It clears on deploy (`sudo systemctl restart power-map`, whose
`ExecStartPre` applies the schema), so the rule is: **restart promptly after
merging a schema change**, and read a parity failure naming only objects your
branch adds as this window rather than as drift. #458 hit it (the new
`reconcile_seeded_slugs` function). To confirm which it is, run the audit
manually and check whether the reported objects are all new in the branch.

Function/trigger defs are PG-version-formatted, so on a **PG major mismatch**
between reference and target those two kinds are skipped (loud WARNING) rather
than misreported as drift; constraints are version-stable and always diff. Keep
the reference on prod's major (point `PARITY_REFERENCE_URL` at a same-major DB).
See `docs/SCHEMA_INDEXES.md` §"Unique Indexes" for why a fresh-DB-only unit guard
can't replace it.

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.audit_schema_constraint_parity
# Gold-standard reference: a scratch DB freshly built from empty via apply_schema
uv run "${env_args[@]}" python -m scripts.audit_schema_constraint_parity --reference-url "$SCRATCH_URL"
```

Scheduled (production): a daily systemd timer. Install / update:

```bash
sudo cp infra/power-map-schema-parity.service infra/power-map-schema-parity.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-schema-parity.timer

# Inspect
systemctl list-timers power-map-schema-parity.timer    # next/last run
sudo systemctl start power-map-schema-parity.service   # run once, now
sudo journalctl -u power-map-schema-parity -f          # drift report on failure
```

---

## role / role_assignment / citation ancillary orphan audit & cleanup (issues #324, #326, #319)


Polymorphic ancillary keyed on `(entity_type, entity_id)` with no FK — for
`role_assignment` (`links` / `contact_methods` / `field_confidence` /
`identifiers`, #324), for `role` (`links` / `contact_methods`, #326), and for
`citations` (all seven citable entity types, #319) — could be orphaned when a merge
or delete drops the parent. The merge/delete paths now re-home (or drop) before
deleting; `scripts/audit_ancillary_orphans.py` is the continuous guard over **all
three** scopes (breakdown namespaced `role.*` / `role_assignment.*` /
`citation.*`). The one-time recovery script stays role_assignment-only (its
heuristics are assignment-specific; role/citation orphans should not occur now that
the write paths are fixed, so any that appear go to manual triage). See
`docs/ANCILLARY.md` §"Merge dedup — role_assignment ancillary re-homing" and
`docs/OBSERVATIONS.md` §"Citations — write semantics".

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Guard: count orphans (exit 3 if any) — read-only
uv run "${env_args[@]}" python -m scripts.audit_ancillary_orphans

# Cleanup: heuristic re-home + redundant-link purge; manual rows reported only
uv run "${env_args[@]}" python -m scripts.cleanup_role_assignment_ancillary_orphans            # dry run
uv run "${env_args[@]}" python -m scripts.cleanup_role_assignment_ancillary_orphans --execute  # supervised
```

Scheduled (production): a daily audit timer. Install / update:

```bash
sudo cp infra/power-map-ancillary-orphans.service infra/power-map-ancillary-orphans.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-ancillary-orphans.timer

# Inspect
systemctl list-timers power-map-ancillary-orphans.timer    # next/last run
sudo systemctl start power-map-ancillary-orphans.service   # run once, now
sudo journalctl -u power-map-ancillary-orphans -f          # orphan breakdown on failure
```
