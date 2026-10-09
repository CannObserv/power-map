# power-map — Recurring Data Audits

The six recurring integrity audits: what each one checks, the categories it
reports, and how to run it. Each is read-only in report mode; the ones that can
repair take `--execute`, and the resolver and target-echo rules are in
`docs/RUNBOOKS.md` §"Operational scripts — dry run by default & target echo".

Four run on a systemd timer — assignment-relationship windows (#301), per-key API
anomaly (#294), schema parity (#315/#331) and ancillary orphans (#324/#326/#319).
Those four exit 3 on findings, so a run surfaces in `systemctl --failed` (#363),
and each carries its own install block below. The org-lifecycle (#307) and
duplicate-assignment (#311) audits are on-demand: no timer, no exit-3. Three
guards at the end carry timers too: readiness (#347), egress IP (#410) and
OpenAPI parity (#618).

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

- `deepened_start` — covering, earlier-start row created later: the
  producer-correction signature; auto-merged by `--execute`
- `subsumed` — covering, earlier-start row created first; auto-merged
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

## Schema-parity audit (issues #315, #331, #632)


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

**Reference ahead of the deploy (#632).** The default reference is
`co_pm_db_test`, and the worktree loop applies a *branch's* schema to it
(`bash scripts/apply-schema.sh --test`) before the PR deploys. So each object
missing in prod is classified against the **deployed** `schema.sql` (the main
checkout's, which the unit runs from; `--deployed-schema` overrides):

- **declared there** → real drift, exit 3;
- **not declared** → the reference is ahead (a pending deploy or a stray branch
  apply): a WARNING naming the objects, exit 4, which the unit's
  `SuccessExitStatus=4` counts as success. #458 and the 2026-10-09 run (PR #629)
  failed on exactly this before.

Matching is by name. Functions and triggers: `CREATE [OR REPLACE] FUNCTION|TRIGGER
name`. Constraints: most never appear in `schema.sql` (Postgres names an inline
`PRIMARY KEY` / `REFERENCES` / `CHECK` / `UNIQUE` itself), so one counts as
declared when its name is written there, or when it has the implicit shape
`<table>_<columns>_<pkey|fkey|key|check|excl|not_null>[N]` on a created table
whose column words all appear (`--` comments stripped; the column test is
schema-wide, so a new constraint on an existing table usually still reads as
drift until it deploys). Ambiguity reads as declared, i.e. as drift; an empty or
unreadable deployed schema fails as misconfigured. Changed bodies (mismatches)
are not classified: a branch that rewrites a function still fails the run until
it deploys, so **restart promptly after merging a schema change**.

An object ahead for more than 3 consecutive runs (`--escalate-after`; at most one
run counts per UTC day) fails with `ESCALATED`: a long-lived or abandoned branch,
not a deploy that is due. `apply-schema.sh --test` from main is additive and will
not remove it: ship the branch, or drop the named objects from the reference once
no worktree needs them, or rebuild it from empty. Per-object streaks live in
`data/schema_parity/reference_ahead.json` (`--state-file`); a run with a streak to
keep that cannot write it fails.

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
sudo journalctl -u power-map-schema-parity -f          # drift / reference-ahead report
```

---

## Polymorphic ancillary orphan audit & cleanup (issues #324, #326, #319, #609)


Polymorphic rows keyed on `(entity_type, entity_id)` with no FK are stranded when
any path drops the entity alone. The merge/delete paths re-home (or drop) them
first; `scripts/audit_ancillary_orphans.py` is the continuous guard: every
polymorphic table × each hard-deletable type it admits (`<type>.<table>`, plus
`<type>.entity_events_linked` for another entity's event linking the id, #611),
and citations on all seven citable types (`citation.*`). Coverage and the
`import_provenance` `action='error'` exclusion → `docs/ANCILLARY.md` §"Hard delete".
The recovery script stays role_assignment-only (its heuristics are
assignment-specific); anything else goes to manual triage.

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

---

## Readiness uptime guard (issue #347)

Not an integrity audit — an uptime guard — but it carries a timer and surfaces
the same way, so its detail lives here with the rest of the roster. Moved from
`docs/COMMANDS.md` § Scheduled timers (#518-class curation); it existed nowhere
else.

`scripts/check_ready.py` probes `GET localhost:8000/ready` every two minutes,
retries once after 10s, and exits 3 only when **both** attempts fail — a lone
blip stays quiet. The journal WARNING carries the reason slug, which is most of
the triage: `no_pool` / `pool_timeout` / `db_error` / `unreachable` /
`probe_timeout` / `http_<code>`. A `pool_timeout` usually means the egress IP
rotated out of DO Trusted Sources → [RUNBOOK_DB_TRIAGE.md](RUNBOOK_DB_TRIAGE.md).

On failure it opens a single `ready-regression` GitHub issue — summary and a
journal pointer only, because the repo is public — and then **stays quiet while
that issue is open**: a comment per run would be ~30/hour. Recovery comments
once and closes it, so the alert state lives in the issue and no local state
file is needed.

It exists because `/ready` was correct and unread throughout the 2026-08-09
outage (#347).

```bash
uv run "${env_args[@]}" python -m scripts.check_ready       # probe once, now
sudo journalctl -u power-map-ready -f                       # reason slug on failure
```

| Override | Effect |
|---|---|
| `READY_PROBE_URL`, `READY_PROBE_TIMEOUT` | target and per-attempt timeout |
| `READY_PROBE_ATTEMPTS`, `READY_PROBE_RETRY_DELAY` | retry shape |
| `READY_CHECK_NO_GH=1` | probe and report, never touch GitHub |
| `READY_CHECK_FORCE_FAIL=1` | force the failure path (exercises the issue flow) |

## Egress-IP drift guard (issue #410)

Also an uptime guard rather than an integrity audit, and here for the same
reason. Moved from `docs/COMMANDS.md` § Scheduled timers; it existed nowhere
else.

`scripts/check_egress_ip.py` runs every five minutes and compares this host's
public egress IP against the cluster's **live** Trusted Sources, read from the
DigitalOcean API (`DO_API_TOKEN`). With no token, or an unreachable API, it
falls back to `EGRESS_EXPECTED_IPS`. A mismatch is exit 3 plus an
`egress-ip-drift` GitHub issue carrying the **new** address.

Reading the allowlist live rather than keeping a hand-maintained copy is what
catches our rule being *removed* — a copy cannot see that — and since #409 there
is nothing to keep in sync. Two readings are deliberately not drift: an **empty**
Trusted Sources list means DO is applying no IP restriction at all, which is
reported rather than alerted; and losing every lookup service is a WARNING at
exit 0, because "I cannot tell" is not "it changed".

It matters because the DO cluster gates on source IP while the exe.dev egress IP
is NAT'd and unpinned, so a rotation kills every DB-backed route — the
2026-08-09 outage (#410). Triage → [RUNBOOK_DB_TRIAGE.md](RUNBOOK_DB_TRIAGE.md).

```bash
uv run "${env_args[@]}" python -m scripts.check_egress_ip   # compare once, now
sudo journalctl -u power-map-egress-ip -f
```

Hatches: `EGRESS_CHECK_NO_GH=1`, `EGRESS_CHECK_FORCE_FAIL=1`.

## OpenAPI parity guard (issue #618)

Not a data audit: a contract guard. It carries a timer and surfaces the same
way as the others, so its detail is kept here.

The gate for the published schema is in the unit tier.
`tests/clients/python/test_drift.py` fails when `app.openapi()` differs from the
committed `clients/python/openapi.json`, or when the generated client differs
from what that snapshot generates. The fix is
`uv run python -m scripts.regenerate_client`, with its diff reviewed in the same
PR (`clients/python/README.md`).

That gate checks the code. `scripts/check_openapi_parity.py` checks what the
production workers actually serve: once a day it fetches
`localhost:8000/openapi.json` and compares it with the snapshot in the main
checkout, which is the deployed commit. A difference exits 3, and the journal
names what differs: `info.version`, paths and `components.schemas`, each list
capped at ten names. It catches a schema that depends on runtime configuration,
a deploy that skipped the gate, and a pull with no restart (the versions then
differ). A fetch failure exits 1, because `/ready`'s guard owns liveness. It is
read-only and opens no GitHub issue; #566's alerting will carry it once that
lands.

It also checks the release tag (#631), the pin consumers install from.
`v<served info.version>` must exist on GitHub and point at a commit whose
`clients/python/openapi.json` equals the served schema, so a tag at the wrong
ref fails as well as a missing one. Either exits 4, and the journal names the
fix (`git tag v<version> && git push origin v<version>` from the main checkout;
`-f` on both to move a wrong one). Drift (3) outranks it. The repo is public,
so the REST API is called without credentials. The check is skipped, with a log
line and no effect on the exit, while `power-map.service` is under 2 h past
`ActiveEnterTimestamp` (a deploy just before the daily run gets until the next
one), and when GitHub is unreachable, rate-limited or answers oddly (WARNING).
Any start opens the window, including the `Persistent=true` catch-up run after
a reboot, so that day's tag check is skipped.

```bash
uv run python -m scripts.check_openapi_parity                 # compare once, now
uv run python -m scripts.check_openapi_parity --no-tag-check --url http://localhost:8001/openapi.json
sudo journalctl -u power-map-openapi-parity -f
```
