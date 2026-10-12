# power-map — Operational Runbooks

Data operations: the importer, the idempotent seeds, the role data-quality sweep
and the TTL prune. Every writer here is dry-run by default and needs `--execute`;
the resolver and target-echo rules that make that uniform are below, in
§"Operational scripts — dry run by default & target echo".

The recurring integrity audits live in `docs/AUDITS.md`, incident triage for an
unreachable database in `docs/RUNBOOK_DB_TRIAGE.md`, and the planned-cutover
checklist in `docs/RUNBOOK_DB_MIGRATION.md`.

---

## Import


```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run (#402) — runs the real pipeline, rolls it back, prints the summary
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv

# Commit — the default DATABASE_URL is PRODUCTION, from any directory
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --execute

# Also run address validation (requires ADDRESS_VALIDATOR_API_KEY)
uv run "${env_args[@]}" python scripts/import_cannabis_observer.py \
    --orgs   data/cannabis_observer/Organizations.csv \
    --people data/cannabis_observer/People.csv \
    --roles  data/cannabis_observer/Roles.csv \
    --validate-addresses --execute

# Options
#   --execute                   Commit (default is a dry run, rolled back)
#   --database-url DSN          Target another database (default: DATABASE_URL)
#   --apply-schema              Apply schema.sql first — fresh DB only; requires
#                               --execute. Prefer scripts/apply-schema.sh (#398).
#   --source-reliability FLOAT  Source reliability score (0.0–1.0, default: 0.8)
#   --imported-by STRING        Importer label (default: cannabis-observer-csv-import)
#   --validate-addresses        Also call /validate for deliverability confirmation
```

The target database is echoed (redacted) to stderr before the connection, so a
run is attributable in scrollback. A dry run parses addresses locally and never
calls address-validator (`local_addresses_only`, #402), so its address fields can
differ from a committed run's. Applying schema DDL was implicit before #402; it
is now opt-in, and `scripts/apply-schema.sh` is the door that carries the
production guards.

Defer an `--execute` run while address-validator is down. Each address row then
pays up to three refused attempts and 1.5 s of retry backoff before it falls back
(#589) — about 25 extra minutes per 1,000 rows — and still lands unstandardized,
marked `fallback: unavailable` in `field_confidence` for re-standardization (#595).
A 429 is different: the import waits out each `Retry-After` — up to 60 s a wait,
three retries — before it falls back the same way (#597).

---

## Seed BCP 47 / ISO 15924 lookup tables (after schema apply, idempotent)


```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Populate bcp47_locales + iso15924_scripts from langcodes + pycountry.
# Idempotent — safe to re-run to pick up registry updates.
# Dry run (reports insert/update counts, writes nothing) without --execute:
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py

# Commit (#402 — the default DATABASE_URL is production):
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py --execute
```

To target the test database, use `scripts/sync-schema-to-do.sh` — it resolves
`TEST_DATABASE_URL` from `/etc/power-map/.env` itself and passes it through as
`--database-url`. `$TEST_DATABASE_URL` is **not** set in your shell by the
`--env-file` flags above; `uv run --env-file` populates the child process only.

Required after a fresh `apply_schema` on a brand-new DB. The FK on
`person_names.locale` / `.script` is active immediately, so any non-NULL
write fails until this script populates the lookup tables. `apply_schema`
logs a WARNING when either lookup table is empty.

---

## Seed jurisdictions from a pre-seed JSON file (idempotent)


Prerequisite: `apply_schema` must be run first so the `jurisdiction_relationship_types` seed
rows (including `is_fully_contained_by`) are present. Without it the script raises
`ValueError: Unknown relationship type`.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — show counts, no DB writes
uv run "${env_args[@]}" python -m scripts.seed_jurisdictions data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json

# Execute — upsert jurisdictions + relationships and commit
uv run "${env_args[@]}" python -m scripts.seed_jurisdictions data/cannabis_observer/2026_06_07-usa_wa-jurisdictions.json --execute
```

Safe to re-run; upserts are idempotent.

---

## Retired cutover seeds (#502)


Two seeders served the #490 cutover and are gone. Recover either from git
history (`git log --diff-filter=D -- scripts/<name>.py`) rather than re-running
an old copy against today's schema.

- `scripts/seed_roles.py` (#263) replayed a WA role seed JSON. Legislative
  roles arrive through usa-wa's published `roles` dataset (#500).
- `scripts/seed_producer_crosswalk.py` (#495) seeded `producer_crosswalk` once
  from usa-wa's anchor export, resolving each PM id through merge history. The
  applier (#499) has been the table's writer since (`docs/SCHEMA.md` § Producer
  crosswalk).

---

## Role data-quality sweep (idempotent, #304)


`scripts/sweep_role_data_quality.py` — follow-on to #266, data-only. Operates **only**
on plain free-text roles (`role_type_id IS NULL AND jurisdiction_id IS NULL`, whose
match key is `(org, lower(title))` = `uq_role_org_title`):

- **Archive non-role artifacts** — `Guest` / `Visitor or Guest`: attendance noise that
  leaks into membership queries. Archives active assignments, then the role (never
  hard-deleted).
- **Normalize typo'd titles** — `Principle` → `Principal`: a misspelling orphans the
  `(org, lower(title))` match key. Renames in place; when the same org already carries
  the canonical role (would collide on `uq_role_org_title`), instead **merges** the typo
  role in — assignments re-pointed with `(person, start_date)` dedup, loser notes +
  role-level ancillary preserved onto the survivor (#324/#326), loser hard-deleted
  (mirrors admin `role_merge`).

The `Participant` disposition, bare `Chairman` normalization, and `Ranking Democratic
Member` typed-fold are deliberately **out of scope** — vocabulary judgment calls for
#266, not a mechanical sweep. Idempotent: canonical titles aren't typo keys and artifact
roles archive once, so re-runs no-op. The dry run distinguishes `would_rename` vs the
destructive `would_merge` per row.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.sweep_role_data_quality            # dry run
uv run "${env_args[@]}" python -m scripts.sweep_role_data_quality --execute  # commit
```

---

## Pull usa-wa dataset snapshots (idempotent, #496)


`scripts/pull_datasets.py` is step 1 of the #490 PM-side pipeline: catalog →
verified, verbatim, versioned local snapshots.

```bash
uv run "${env_args[@]}" python -m scripts.pull_datasets                     # every pinned dataset
uv run "${env_args[@]}" python -m scripts.pull_datasets --dataset persons   # one of them
```

**Auth is an exe.dev VM bearer token in `USA_WA_TOKEN`** (header
`X-Exedev-Authorization`), minted with
`ssh exe.dev ssh-key generate-api-key --vm=usa-wa`. usa-wa's `/datasets` surface
sits behind a **private** proxy, and this is the failure worth knowing: an
absent or wrong token does not produce a 401. The proxy answers 307 with an HTML
login page, so a client that follows redirects and checks only for a 2xx parses
that page as a catalog and reports an empty one. The puller therefore does not
follow redirects, asserts the content type, and names every auth-shaped outcome
as authentication — and the script refuses to start with no token at all.

- **The subscription is the pins (#536).** Each `usa_wa` source in
  `src/core/ingestion/mapping/models/sources.yml` carries `meta.schema_major` and
  `meta.contract_hash` — the contract its model was written against — and those
  sources are the whole subscription, so the nightly takes no flags. `--dataset`
  only narrows it; an unpinned name exits 2. A version whose `contract_hash`
  differs from its pin is refused, not landed; the major only words the refusal
  (moved: the models need a change; same: review, then re-pin). The INCOMPATIBLE
  line names the published schema and contract hash — both values a re-pin
  needs. Re-pinning is a diff to that file, reviewed with the model change it
  implies. Majors are per
  dataset and never compared across datasets (usa-wa#385).
- **Hash-skip.** A version already in the store is not re-fetched; a night with
  nothing new upstream costs one catalog request.
- **Landing is atomic.** Files are verified into a staging directory and moved
  into place, so a version directory exists only when the snapshot in it is
  complete. Presence means completeness. Verification is the length the catalog
  states, then its digest — a truncated transfer says so in bytes rather than as
  two unequal hashes.
- **A snapshot lands without `datapackage.json` only on a 404**, the publisher
  stating there is none (logged at WARNING). Any other failure fetching it — a
  500, an expired token — fails the dataset instead, because hash-skip would
  otherwise make a one-second blip a permanently schema-less snapshot for the
  life of that version.
- **Each version carries `snapshot.json`** — name, version, schema version,
  digest, contract hash, row count, `generated_at`. That is what lets a consumer verify a
  snapshot without a second catalog fetch that may answer with a newer version
  by then.
- **Pruning spares the version just landed**, whatever `--keep` says.
- **The catalog carries the publisher's heartbeat (#551, usa-wa#386).**
  `checked_at` says the publisher completed a run — it advances every night
  whether or not anything minted — and `stale_after` is the deadline for the
  next one (the 08:00 run plus 45 minutes of grace). Both are written verbatim
  to `pull.json` at the store root, which the build reads — beside `offered`,
  the version the catalog names for every dataset, landed or not (#535). A fresh `checked_at`
  covers the **publisher**, not everything upstream of it: a failed source
  harvest and a registrar conflict both leave it fresh. A catalog published
  before usa-wa#386 carries neither, and absent is never late.
- **Exit 1** on a failed verification, a contract its pin refuses, a subscribed
  dataset the catalog does not carry, or a pull past `stale_after`. The last two
  matter for the same reason: a renamed dataset that pulls nothing, and a
  producer behind its clock, are both indistinguishable from a quiet night —
  every dataset reads `unchanged` and the run is otherwise green.

Writes only into `data/usa_wa_snapshots/` (gitignored) — never the database, so
there is no `--execute` gate here. The gated step is the applier (#499).

---

## Build and apply the desired state (#497, #499)


Moved to [`docs/RUNBOOK_DESIRED_STATE.md`](RUNBOOK_DESIRED_STATE.md): the
three-step chain (export → `dbt build` → dry-run applier), the ownership
manifest, the applier's entry kinds, verdicts, exit codes and thresholds, the
flip's flags, and the first production measurement.

---

## Outbox + tombstone TTL prune (issue #204)


`scripts/prune_outbox.py` deletes rows from **three** append-only tables, each
past **its own** retention window (`Retention` in `src/core/maintenance.py`, #502):

| Table | Flag | Default | What the window bounds |
|---|---|---|---|
| `entity_changes` (the outbox) | `--changes-retention-days` | 90 | Nothing reads it since the change feed retired (#502); pruned until it is dropped (#651) |
| `deleted_entities` (bare-delete tombstones) | `--tombstone-retention-days` | 90 | How long a deleted id answers `410` before `404` (`docs/PUBLIC_API.md` § Retired ids) |
| `api_request_log` (#260) | `--request-log-retention-days` | 90 | The request log's PII footprint (`docs/CONVENTIONS.md`) |

**Merge tombstones (`merged_into` set) are exempt at any age (#607):** they drive
the detail GETs' `410` + `merged_into`, so a merge stays followable for good. One
row per merge, so the table stays small. The installed unit names no window, so
the defaults govern; the #495 104-day hold was released in #502.

Manual run:

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — count each table's eligible rows
uv run "${env_args[@]}" python -m scripts.prune_outbox

# Execute — delete (optionally override one table's window)
uv run "${env_args[@]}" python -m scripts.prune_outbox --execute
uv run "${env_args[@]}" python -m scripts.prune_outbox --execute --request-log-retention-days 30
```

Scheduled (production): a daily systemd timer runs `--execute`. Install / update:

```bash
sudo cp infra/power-map-prune.service infra/power-map-prune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-prune.timer

# Inspect
systemctl list-timers power-map-prune.timer   # next/last run
sudo systemctl start power-map-prune.service   # run once, now
sudo journalctl -u power-map-prune -f          # logs (rows pruned per run)
```

## Operational scripts — dry run by default & target echo (#402)


`DATABASE_URL` comes from `/etc/power-map/.env` and resolves to **production**
from any directory — main checkout, worktree, anywhere on the VM. Nothing about
a `scripts/…` invocation signals that. Two rules follow, and they are separate
concerns: the gate stops an unintended write, the echo makes an intended one
attributable afterwards.

**Every script that writes gates the write behind `--execute`.** The bare
invocation is read-only and reports what would change. #398 fixed
`apply-schema.sh`, the one script that wrote unconditionally; #402 found two
more (`import_cannabis_observer.py`, `seed_locales_scripts.py`) and closed
them. The convention was believed universal before that and was enforced by
nothing — #399's AST sweep is what makes it stop depending on memory.

**Every script echoes its target before connecting**, via `add_dsn_args()` +
`resolve_dsn()` from `scripts/_dsn.py`:

```
target: co_pm_db_production_user@co-pm-db-1-….ondigitalocean.com:25060/co_pm_db_production (production)
```

The label is derived by matching `(host, port, dbname)` — **not** the DSN
string. Production is reached as two different users (`DATABASE_URL` as the app
user, `MIGRATIONS_DATABASE_URL` as the migrations user); string equality would
label a migrations DSN `unknown`. Anything unmatched is
`unknown — assume production`, never `test`: the consequence of guessing wrong
runs one way.

The uniform flags (#399):

| Flag | Effect |
|---|---|
| *(none)* | `DATABASE_URL` — production |
| `--database-url DSN` | that DSN |
| `--test` | `TEST_DATABASE_URL`; **hard-errors when unset** — never falls back to `DATABASE_URL`, which would be a production write dressed as a test write |

**Resolve last.** The echo means "about to connect", so `resolve_dsn` goes
*after* any input validation that can abort the run — otherwise the journal
records a database the run never opened, which is the false attribution the
echo exists to prevent. `check_api_anomalies` extends this to its
`threshold <= 0` short-circuit: a disabled run resolves nothing.

Passing `--test` and `--database-url` together is an error, not a precedence
rule. A script whose target flags are domain-named (`audit_schema_constraint_parity`
takes `--target-url` / `--reference-url`) uses `default_dsn()` for the default
and calls `echo_target(..., role=…)` per connection, so each gets its own line.

**Every script builds its parser with `build_parser(__doc__)`** from
`scripts/_dsn.py` (#509). A bare `argparse.ArgumentParser(description=__doc__)`
lets argparse **reflow** the docstring, so a `Usage:` block and an
`Exit codes:` list arrive in `--help` as one paragraph — destroying the
invocation examples, which are the part an operator opened `--help` for.
`build_parser` sets `formatter_class=argparse.RawDescriptionHelpFormatter` and
passes through `prog`, `epilog` and the rest, so complying is shorter than not.

Four scripts legitimately pass something else and are out of scope: a one-line
literal (`check_egress_ip`, `check_ready`, `import_cannabis_observer`) or the
docstring's first line only (`seed_442_historical_parties`) — a single line has
no structure to destroy. The sweep is scoped to `description=__doc__` for that
reason rather than carrying an exemption list.

`redact_dsn()` drops the password *and* the query string, and returns `None`
for anything that is not a parseable URL. **Callers never fall back to printing
the raw string**: `urlparse` hands back a libpq keyword/value DSN
(`host=… password=…`) with the credentials in `path`, so a "best effort" echo
would put the password in the journal. An absent database name renders `?`.

Two shapes of dry run, both legitimate:

| Shape | Used by | Note |
|---|---|---|
| Read-only preview | `seed_locales_scripts.py`, the `audit_*` scripts | Classify against current state; write nothing |
| Real work, rolled back | `import_cannabis_observer.py` | The summary printed is the summary `--execute` produces |

The rolled-back shape has one trap: **side effects outside the transaction do
not roll back.** The importer parses addresses locally on a dry run
(`ImportConfig.local_addresses_only`) rather than spending the rate-limited
external validator's quota on a run that changes nothing — standardization
fires whenever `ADDRESS_VALIDATOR_API_KEY` is set, independent of
`--validate-addresses`, so that flag is the only lever. The cost is that
address fields in a preview may differ from a committed run; the dry-run notice
says so.

Schema DDL is never implicit. `scripts/apply-schema.sh` owns applying
`schema.sql` and carries the #398 production guards; the importer's
`--apply-schema` is opt-in and requires `--execute`, because DDL inside a run
about to be rolled back would be a lie.

All three rules are enforced by `tests/scripts/test_dsn_sweep.py`, an AST sweep
over every `scripts/*.py`: it connects ⇒ goes through `_dsn.py`; nobody reads
`DATABASE_URL` directly; write SQL ⇒ declares `--execute`. **It has no
allowlist** — an exemption set is a place for a live script to hide. If a new
script genuinely cannot comply, change the sweep with a reason in the diff.

`apply-schema.sh` deliberately keeps its **own copy** of the redaction logic
rather than importing `_dsn.py` — it runs as `ExecStartPre` on the systemd
unit, where an import failure would mean a failed production restart. The two
copies are pinned in agreement by
`tests/scripts/test_dsn.py::test_redaction_matches_apply_schema_sh`.

---

## Completed runbooks

One-off migrations and backfills already run against production are archived in
`docs/archive/RUNBOOKS_COMPLETED.md` — kept verbatim for provenance, excluded from
the live context surface. Nothing there is pending.
