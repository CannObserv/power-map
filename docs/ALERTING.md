# power-map — Alerting

How a failure reaches a person: every scheduled timer's co-status check-in, and
the API service's own `OnFailure=` dispatch, both delivered through Notifier
(issue #566). What each timer checks is in [AUDITS.md](AUDITS.md) and the
roster in [COMMANDS.md § Scheduled timers](COMMANDS.md#scheduled-timers);
`systemctl --failed` stays the ambient signal beside both paths.

---

## The timers' check-ins

Each of the 12 `power-map-*.timer` services runs
`scripts/status_checkin.py` as `ExecStopPost=` against its own co-status monitor
(tenant `co-power-map`, CannObserv/status#36; the unit's comment names it). The
hook reads systemd's verdict: `SERVICE_RESULT=success` is `ok`, a declared
`SuccessExitStatus=4` warning included (schema-parity's reference-ahead,
infra-drift's comment-only); anything else is `alert`, its `kind` systemd's
result (`exit-code`, `timeout`, `signal`, …) or the script's own name for the
exit from the unit's `STATUS_EXIT_KINDS` (`3:drift`), and `metadata.fault = kind`
so a change of failure reports at once. A run that never happens (timer
disabled, unit not reinstalled, VM down, tailnet gone) goes `missing` there.
Root disk is the one exception (#705): the cohort's canonical sensor,
`cannobserv-disk-headroom`, checks in itself to `co-power-map-disk-headroom`
(`low-headroom`, `gc-failed`, `gc-stale`), alerting at 85% and held until ≤ 80%
→ [AUDITS.md § Root disk](AUDITS.md#root-disk-the-cohort-disk-gc-issue-705).
`tests/test_infra_alerting.py` fails on a timer without the hook.

- **Best-effort.** The command carries `-` and always exits 0: a check-in that
  cannot be sent is a journal WARNING, never a change to the unit's result.
  That's true by priority too, not only by text: each line carries its syslog
  `<N>` on the journal, so `journalctl -p warning` finds it (#692).
- **Standard library, `/usr/bin/python3`.** It must work when the venv or `uv`
  is what broke, so it imports nothing from the repo but its stdlib sibling
  `scripts/_journal.py`, the journal-priority helper. That helper loads the one rule, the stdlib-only
  `src/core/journal.py` that `configure_logging()` also uses, by file path
  (#704). Without either (missing, broken, or dropped from `sys.path` by
  `python3 -I`/`-P` or `PYTHONSAFEPATH`) the check-in still goes out; only the
  priority is lost, and the run logs that once as a WARNING.
- **No journal text.** The body is one sentence and `journalctl -u <unit> -n 100`;
  this repo is public, and the detail stays on the host.
- **The key is a credential**, `LoadCredential=` from a root-only file, never an
  env var. Each unit also sets `SetCredential=status-checkin:\n`: without that
  fallback a missing file fails the unit (243). An empty key or an empty
  `STATUS_MONITOR_ID` skips the check-in, logged.

No timer opens a GitHub issue: `ready` and `egress-ip` stopped, and #740 retired
the weekly a11y sweep, whose tiers now gate the ship.

## Transient failures (#833)

On 2026-11-17 a ten-minute network outage cost each daily unit a day: every
timer failed on its first error. The rule now: **a unit that reads the network
waits a transient failure out inside the run and checks in once**. A permanent
failure (a 4xx, bad credentials, a TLS verify failure) still alerts in the same run.

| Unit | Cadence | How it rides out a blip | `TimeoutStartSec` |
|---|---|---|---|
| ready, egress-ip | 2 / 5 min | the next tick (ready also probes twice; egress-ip retries the DO API, #839) | 120 |
| infra-drift | daily | no network but the check-in | 120 |
| openapi-parity | daily | none: `/openapi.json` is local, and the tag check's skip is #824's accepted limit | 120 |
| anomaly | hourly | DB connect retried in-process | 900 |
| ancillary-orphans, assignment-rel-windows, prune, seat-occupancy, schema-parity | daily | DB connect retried in-process | 900 |
| classification | daily | DB connect and every Jev request, on one budget; Jev failing alerts only when permanent or stale 48 h | 1800 |
| datasets-pull | daily 09:00 | every request retried in-process | 1260 |
| desired-state | daily 09:30 | the connect in export **and** in apply | 1800 |

- **In-process, never `Restart=`.** `src/core/retry.py`: one `Backoff` per run,
  15 → 240 s, 645 s in all, shared by every call in the run; once a call gives
  up, every later call fails at once, unattempted. Only the connect
  or the GET is retried, never a query. Each failure is judged by the library
  that raised it (httpx and httpx2 share no classes, #831): an `OSError` or
  a transport error retries, a TLS error anywhere in its chain doesn't, a status by
  #841's rule (5xx/429 retry; 401, 403, 404 don't). `Restart=` would check in after every
  attempt (`alert`, then `ok`), retry a real finding, and re-run the whole unit.
- **What the monitor sees during a retry: nothing.** The hook runs once, after
  the final verdict. A run that recovered logs one WARNING (`… succeeded after
  N attempts`), so `journalctl -p warning` still finds the degraded night.
- **Each budget is a test.** `tests/test_infra_retry.py` gives every timer a
  row (a new timer without one fails) and holds each in-process unit's worst case
  (every attempt timing out, the whole budget slept) inside its
  `TimeoutStartSec`, that inside its monitor's grace, and the pull's inside the
  gap before desired-state.
- **The check-in's own budget stays small** (2 attempts, at most 22 s, under the
  default 90 s `TimeoutStopSec`). The run has already waited, so the network is
  usually back by the time the hook runs. If an outage outlasts the retry, the alert can
  be lost too. The monitor then reads `missing` at its grace: about 2 h after a daily
  run, about 30 min after the hourly one (status#36). No spool: a daily unit's
  next run is a day away.

## The API's OnFailure= dispatch

`power-map.service` carries
`OnFailure=power-map-notify-failure@%n.service` (CannObserv/notifier#114). The
handler logs a crit line tagged `power-map-unit-failure` first, then runs
`scripts/notify_unit_failure.py`: one dispatch per failed invocation
(`idempotency_key = {unit}:{invocation_id}`), variables `unit`, `result`, `host`
and `journal_cmd`, refused unless Notifier's `/health` says `production`.
`RestartMode=direct` keeps each restart out of `failed`, so the handler fires
once, when 10 starts land inside 600 s (a refused `ExecStartPre` schema apply
included) and systemd gives up. Only a fast failure trips it. A connect that
hangs because the DB is unreachable takes about 65 s per attempt, so it never
trips, and that loop is kept on purpose (`docs/RUNBOOK_DB_TRIAGE.md` § Restarting
during an outage, #748). The service then stays down until
`sudo systemctl reset-failed power-map && bash scripts/pre-restart-check.sh && sudo systemctl start power-map`. The
stored template's wording is `scripts/notifier/unit-failure.json`.

The handler runs `.venv/bin/python`, not `uv run`, so a `notifier-client` pin
move reaches it only at the API restart's `ExecStartPre` `uv sync`: pull and
restart back-to-back, or new code runs on the old SDK in between (#831).

## Install and prove

The ids are not secrets and live in the units: each `STATUS_MONITOR_ID` from
status#36 (then flip `MONITOR_IDS_PENDING` in `tests/test_infra_alerting.py`), and
the handler's `NOTIFIER_TEMPLATE_ID` and `NOTIFIER_CHANNEL_IDS` from the publisher
and notifier#114. Then re-run each unit's install line.

```bash
# Keys, once: the operator writes each terminal to terminal, never echoed
sudo install -m 600 -o root -g root /dev/null /etc/power-map/status-api.key
sudo install -m 600 -o root -g root /dev/null /etc/power-map/notifier-api.key
test "$(curl -s http://status:9000/health | jq -r .environment)" = production

# The template, after an edit to the JSON (dry run without --execute)
sudo cat /etc/power-map/notifier-api.key | \
  uv run python -m scripts.publish_notifier_template --execute [--template-id <ULID>]

# A deliberate `test` alert on one monitor; the timer's next ok clears it
sudo systemd-run --wait --pipe -p LoadCredential=status-checkin:/etc/power-map/status-api.key \
  -E STATUS_MONITOR_ID=<id> /usr/bin/python3 /home/exedev/power-map/scripts/status_checkin.py \
  power-map-ready.service --test

sudo journalctl -u 'power-map-*' -g status_checkin      # every check-in, sent or not
sudo journalctl -u 'power-map-*' -g status_checkin -p warning  # not sent, or skipped
sudo journalctl -t power-map-unit-failure               # the handler's fallback line
sudo journalctl -u 'power-map*' -p warning --since -7d  # every unit's WARNING and up (#704)
```

