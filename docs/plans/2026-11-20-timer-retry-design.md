# Timer retries for transient network failures — design (#833)

Status: **proposed**, awaiting operator approval. Epic #818. Target: one PR, patch 0.73.3.

## Goal

A network blip of up to 10 minutes must not cost a daily run. A daily unit that
reads the network waits a transient failure out **inside the run** and reports
one verdict. A permanent failure still alerts in the same run. The policy is
written in `docs/ALERTING.md` and a test fails on a new timer that doesn't
declare its place in it.

## Corrections to the brief `[traced]` on `origin/main` `d5ccb402`

- **9 direct `asyncpg.connect(` calls across 8 timer scripts, not 7.**
  desired-state connects twice (`export_pm_tables` L115 *and*
  `apply_desired_state` L294, separate processes), and schema-parity connects
  twice in one process (reference L389, target L391). The 7 *units* are right.
- **The asyncpg connect has no timeout of its own**, so asyncpg's default of 60 s
  applies. A black-holed connect costs a minute per attempt, which the budget has
  to count.
- **The daily units have no `TimeoutStartSec`.** systemd turns the start timeout
  off for `Type=oneshot`, so today only ready, egress-ip, infra-drift and
  openapi-parity are bounded. A budget test needs a bound to test against, so
  every retried unit gets one (a unit change: reinstall + `daemon-reload`).
- **co-status grace `[stated]`, status#36:** daily monitors: interval 86400 s,
  grace 7200 s. Hourly (anomaly): 3600 s / 1800 s. A daily run whose alert is lost
  goes `missing` about **2 h** after it ran. An hourly one does at about **30 min**.
- **Observed run times** (journal, 11-19): every DB audit about 1 s, the pull
  under 1 s on an unchanged night, desired-state 40 s. The retry budget
  sets each unit's worst case.

## Q1. Where the retry lives: **(b) in-process**, with no systemd `Restart=` on any timer

**Recommendation:** in-process retry of the network call only, through one shared
helper. No timer service carries `Restart=`, and a test enforces that.

Why not (a), systemd `Restart=on-failure`:
1. **Flap at the monitor.** `ExecStopPost=` runs after every attempt, and its env
   (`SERVICE_RESULT`, `EXIT_CODE`, `EXIT_STATUS`) can't tell a non-final attempt
   apart. Telling it apart would need `systemctl show -p NRestarts` compared with
   `StartLimitBurst` from a stdlib hook. That's brittle, and it fails silently.
2. **It retries permanent failures too.** A real `drift` exit 3 or a 401 would
   re-run until the burst is spent, unless every unit keeps a
   `RestartPreventExitStatus=` list in step with its script. That's against
   "a permanent failure still alerts in the same run".
3. **It re-runs the whole unit.** desired-state would start again from
   `export_pm_tables`, and `prune --execute` would repeat its writes. (b) retries
   only the connect or the GET, which wrote nothing.
4. **The chain loses its ordering** `[inferred]`. Between attempts the pull sits in
   `auto-restart` with no start job queued, so desired-state's
   `After=power-map-datasets-pull.service` has nothing to wait behind. Under (b) the pull's
   start job stays open for the whole retry, so a desired-state start that
   comes due meanwhile waits for it.

Why not (c): the only units that (a) would suit are the 2- and 5-minute ones,
and their next tick already is the retry (Q2).

### The helper: `src/core/retry.py`

It goes in `src/core/`, not `scripts/`, because `src/core/ingestion/datasets.py`
needs it and `src/` must not import `scripts/`.

- **`Backoff`**: the run's retry budget, `DELAYS = (15, 30, 60, 120, 180, 240)` s,
  645 s in all. **One per run, shared by every retried call in it.** A call that
  succeeds doesn't refill it, so a run's total waiting can never exceed 645 s,
  however many calls it makes. That's what makes the pull's worst case
  computable.
- **`retry_async(call, *, transient, backoff, label)`**: it tries the call, and on a
  `transient(exc)` failure it sleeps the next delay and tries again. It re-raises at
  once on a permanent failure, or when the budget is spent. Each retry is
  logged at INFO with the attempt count and the exception class. A call that
  succeeds after one or more retries logs one WARNING ("succeeded after N attempts,
  Xs"), so a degraded night shows in `journalctl -p warning`, as #704 intends.
- **`connect_with_retry(dsn, *, backoff, timeout=15)`**: `asyncpg.connect`
  with a 15 s per-attempt timeout instead of asyncpg's default 60 s. The DB's
  RTT is about 10 ms, so 15 s is still generous.
- **Classifiers, one per library** (from #831's note: httpx and httpx2 share no
  classes, so each call site names its own library):
  - `is_transient_connect(exc)`, for asyncpg and the OS. Transient: any `OSError`
    (`socket.gaierror`, `ConnectionRefusedError`, `ConnectionResetError`,
    `TimeoutError`), `asyncpg.CannotConnectNowError` and
    `asyncpg.TooManyConnectionsError`. Permanent: **any `ssl.SSLError`** (it *is*
    an `OSError`, so it has to be checked first: a verify-full failure (#667)
    won't change in 10 minutes), `InvalidPasswordError`,
    `InvalidAuthorizationSpecificationError` (the #680 CONNECT allowlist) and
    `InvalidCatalogNameError`.
  - `is_transient_httpx(exc)`, for plain `httpx`. Transient: `TimeoutException`,
    `NetworkError` and `RemoteProtocolError`, **unless** the `__cause__`/`__context__`
    chain holds an `ssl.SSLError`: httpx wraps a TLS verify failure in
    `ConnectError`. Status is judged as in #841: 5xx and 429 retry; 401, 403, 404,
    a redirect and the proxy's login page don't.
  - **All `gaierror`s retry, `EAI_NONAME` included.** 11-17 was `EAI_AGAIN`, but
    a resolver with no upstream doesn't promise that, and the costs aren't
    equal: a mistyped host alerts 11 minutes late, while a misclassified outage
    loses a day.

What isn't retried: queries on a connection that's already open (one may have
written, and the app pool's resilience is out of scope); argparse and config
errors; anything after the network step.

## Q2. Which units, each one's retry window and its `TimeoutStartSec` budget

| Unit | Cadence | Policy | Worst case `[computed]` | `TimeoutStartSec` |
|---|---|---|---|---|
| ready | 2 min | **next tick**, as now (2 attempts × 10 s) | as now | 120 (unchanged) |
| egress-ip | 5 min | **next tick**, plus #841's DO API retry | as now | 120 (unchanged) |
| infra-drift | daily | **no network**: it reads local files; only its check-in goes out | n/a | 120 (unchanged) |
| openapi-parity | daily | **accepted skip** (Q5); `/openapi.json` is localhost | as now | 120 (unchanged) |
| anomaly | hourly | in-process, DB connect | 645 + 7 × 15 = **750 s** | **900** (new) |
| ancillary-orphans | daily | in-process, DB connect | 750 s | **900** (new) |
| assignment-rel-windows | daily | in-process, DB connect | 750 s | **900** (new) |
| prune | daily | in-process, DB connect | 750 s | **900** (new) |
| seat-occupancy | daily | in-process, DB connect | 750 s | **900** (new) |
| schema-parity | daily | in-process, 2 connects, **one shared budget** | 645 + 8 × 15 = 765 s | **900** (new) |
| datasets-pull | daily 09:00 | in-process, httpx `_get` (catalog + every file) | 645 + 7 × 60 = **1065 s** | **1260** (new) |
| desired-state | daily 09:30 | in-process, connect in export **and** apply (2 processes, 2 budgets) | 2 × 750 + 40 = 1540 s | **1800** (new) |

- **Ride-out:** the shortest window is the budget's sleep alone, 645 s, which is
  more than 600 s. That's true however fast each attempt fails.
- **The chain:** the pull fires at 09:00:00–09:05:00 and desired-state at
  09:30:00–09:35:00, so the narrowest gap is **1500 s**. The pull's
  `TimeoutStartSec=1260` ends it by 09:26 at the latest, so its worst case always
  finishes before desired-state can fire. The pull client becomes
  `httpx.Timeout(60, connect=15)`, but the budget counts each failed attempt at
  the 60 s read timeout, to stay conservative.
- **Hourly anomaly:** the same budget. 900 s is well inside its 3600 s
  cadence, and **750 s is inside its 1800 s grace**, so a retrying run never shows
  `missing`.
- **What the monitor sees during a retry:** nothing. The hook runs once, after
  the final verdict, at most 1800 s late. That's inside every monitor's grace.
- `_get`'s status handling gets restructured so that a 5xx or 429 is judged
  transient *before* `_check_response` turns it into a `CatalogError`.

## Q3. Persistent "cannot verify" / "could not determine": **escalate by the cause's permanence, not by a run count**

**Recommendation:** no state file and no N-runs counter. Judge the cause, using
#841's own rule (`_is_transient`):

- **A permanent cause alerts in the same run**, with a new exit 5 →
  `STATUS_EXIT_KINDS=3:drift,5:unreadable`. The cases: the DO API answered
  401/403/404, the cluster doesn't exist (`LookupError`), or a bad body
  (`ValueError`). The guard is blind until someone acts, which is #841's named
  trade-off (a revoked token stays quiet). The fallback is still consulted and
  logged, so the journal says whether the address looked fine. This covers the
  fallback-*agrees* case too, which today passes as `ok` with source
  `API_FALLBACK`.
- **A transient cause stays quiet**, as now. That's the DO API still failing after #841's
  3 attempts, or every echo service failing ("could not determine the egress
  IP"). At a 5-minute cadence the next tick is the retry, and `/ready` (every
  2 min) catches a real cut-off.

Why not "N runs, then `unreadable`": it needs persisted state, so a new
`StateDirectory=`, a file format and a reset rule, for the one case (a revoked
or mis-scoped token) that permanence already catches in the first run. What a
counter would add is a DO API down for hours, and #841 decided that DO's
own outage isn't ours to page on. If co-status ever offers "alert after N
consecutive *degraded* check-ins", that's the right home for it. It goes under
Known limits.

## Q4. status_checkin's budget: **keep 2 attempts, accept `missing`, document each cadence's grace**

**Recommendation:** no change to the hook's retry and no spool. Add a budget test
and the documentation.

- With (b), the wait happens **before** the verdict. A daily run that hits a
  10-minute outage spends up to 645 s retrying, so by the time
  `ExecStopPost=` runs, the network the check-in needs is most likely back.
  The hook's own 22 s worst case only has to cover what's left over.
- **Spool rejected.** A daily unit's next run is 24 h away. By then the monitor
  has gone `missing` at +2 h, and the spooled alert would arrive beside a fresh
  verdict, describing a run that's a day old.
- **Longer hook retry rejected.** `ExecStopPost=` runs under `TimeoutStopSec`
  (default 90 s, which no unit sets), and the hook is the stdlib path that has
  to stay small. Stretching it to about 80 s buys little once the run has already waited.
- **Residual, documented in ALERTING.md:** if an outage outlasts the run's retry,
  its alert can be lost too, and then the monitor reads `missing` at about +2 h (daily)
  or about +30 min (hourly). That's the designed signal (status#36: "a skipped day is
  the thing we want to hear about").
- **Budget test:** `ATTEMPTS × TIMEOUT_SECONDS + (ATTEMPTS − 1) × RETRY_DELAY_SECONDS`
  ≤ 90 s, the default `TimeoutStopSec`, with headroom, and no timer may set a
  lower `TimeoutStopSec`.

## Q5. openapi-parity's git skip: **leave it as #824's accepted limit**

**Recommendation:** not folded in. Recorded under Known limits.

- The thing it checks moves slowly: a tag changes only at a release. A skipped
  day costs nothing downstream (no chain, no streak) and the next day's run checks it.
  The pull is different, because a skipped day there costs the dataset day and the `--execute` streak.
- `git ls-remote` exits 128 both for "could not resolve host" and for permanent
  causes. Telling them apart means matching stderr text, which is fragile and
  locale-dependent.
- Its main fetch is localhost `/openapi.json`, and `/ready` owns liveness.

## Q6. Proof without the network, and the budget-test pattern

Every test runs the **real** client against a failure produced locally, and
asserts the **verdict** (exit code or report), the attempt count and the slept
delays. The sleep is injected and records rather than waits. The worked example is #831's
`test_an_unreachable_notifier_is_an_expected_failure_with_the_real_sdk`.

- **Transient, real asyncpg:** a refused loopback port (bind, read the port,
  close), giving `ConnectionRefusedError`, and an `.invalid` host, giving `gaierror`.
  Assertions: 7 attempts, the slept delays equal `DELAYS`, and one final exit 1 from
  the script's `main`.
- **Transient then recovered, real httpx:** a loopback `http.server` thread
  answering 503, 503, then a valid catalog. Assertions: `pull_datasets.main()`
  exits 0 with one verdict and logs the recovery WARNING.
- **Permanent, at once:** the same server answering 401, 404 or the login page →
  one attempt, no sleep, `CatalogError` in the same run. asyncpg's permanent classes
  and the `ssl.SSLError` cases are classifier unit tests on constructed exceptions.
  One integration test (run alone, after `apply-schema.sh --test`) connects to
  `TEST_DATABASE_URL` with a wrong password, to prove `InvalidPasswordError`
  isn't retried by the real client.
- **Egress (Q3):** the existing `_flaky` seam with `_http_error(401)` → exit 5;
  `TimeoutError` exhausted → exit 0 `cannot verify`, as now.
- **The budget test, per retried unit**, in `tests/test_infra_retry.py`, next to
  `test_infra_alerting.py`:
  - `RETRY_POLICY`: one row per timer (`next-tick`, `no-network`,
    `accepted-skip`, or `in-process` with its ExecStart modules, per-attempt
    timeout, connects-per-process and co-status grace). **A timer with no row
    fails**, which is what enforces the policy for any new timer.
  - For each `in-process` row: parse `TimeoutStartSec` from the unit, compute
    `sum(DELAYS) + (len(DELAYS) + 1) × attempt_timeout × processes`, plus the
    run's normal time and 120 s of headroom, and assert it's ≤ the limit and ≤ the grace.
    Also `sum(DELAYS) ≥ 600`, the ride-out guarantee.
  - The chain: the pull's `TimeoutStartSec` must be ≤ the gap derived from both
    timers' `OnCalendar` and `RandomizedDelaySec`.
  - No timer service carries `Restart=` (Q1).
  - An AST sweep: no `in-process` ExecStart module calls `asyncpg.connect(`
    directly, so they all go through `connect_with_retry`.

## Out of scope

Why the VM's egress drops (exe.dev's side). The API's `OnFailure=` path (#831).
The long-lived app pool. Retrying the other `asyncpg.connect(` calls in `scripts/` (41 in
all, 9 of them in timer scripts): a person runs those and retries by hand. Stateful escalation (Q3). openapi-parity's git skip (Q5).

## Host steps (after merge, for the PR's closing comment)

Nine units change: `TimeoutStartSec=` on the eight `in-process` units, plus
egress-ip's `STATUS_EXIT_KINDS`. From the main checkout, after the pull:

```bash
for u in anomaly ancillary-orphans assignment-rel-windows prune seat-occupancy \
         schema-parity datasets-pull desired-state egress-ip; do
  sudo cp "infra/power-map-$u.service" /etc/systemd/system/
done
sudo systemctl daemon-reload
uv run python -m scripts.check_infra_drift   # exit 0: no drift
```

No API restart is needed: the timers' scripts are loaded fresh on each run.
