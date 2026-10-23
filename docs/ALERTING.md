# power-map — Alerting

How a failure reaches a person: every scheduled timer's co-status check-in, and
the API service's own `OnFailure=` dispatch, both delivered through Notifier
(issue #566). What each timer checks is in [AUDITS.md](AUDITS.md) and the
roster in [COMMANDS.md § Scheduled timers](COMMANDS.md#scheduled-timers);
`systemctl --failed` stays the ambient signal beside both paths.

---

## The timers' check-ins

Each of the 15 `power-map-*.timer` services runs
`scripts/status_checkin.py` as `ExecStopPost=` against its own co-status monitor
(tenant `co-power-map`, CannObserv/status#36, and #48 for the two disk timers;
the unit's comment names it). The
hook reads systemd's verdict: `SERVICE_RESULT=success` is `ok`, a declared
`SuccessExitStatus=4` warning included (schema-parity's reference-ahead,
infra-drift's comment-only); anything else is `alert`, its `kind` systemd's
result (`exit-code`, `timeout`, `signal`, …) or the script's own name for the
exit from the unit's `STATUS_EXIT_KINDS` (`3:drift`), and `metadata.fault = kind`
so a change of failure reports at once. A run that never happens (timer
disabled, unit not reinstalled, VM down, tailnet gone) goes `missing` there.
The disk-headroom guard holds an alert (#690): it alerts at 85% and keeps
exiting 3 until `/` is at or below 80%, so its `ok` means recovered, not a
one-point dip under the line.
`tests/test_infra_alerting.py` fails on a timer without the hook.

- **Best-effort.** The command carries `-` and always exits 0: a check-in that
  cannot be sent is a journal WARNING, never a change to the unit's result.
- **Standard library, `/usr/bin/python3`.** It must work when the venv or `uv`
  is what broke, so it imports nothing from the repo.
- **No journal text.** The body is one sentence and `journalctl -u <unit> -n 100`;
  this repo is public, and the detail stays on the host.
- **The key is a credential**, `LoadCredential=` from a root-only file, never an
  env var. Each unit also sets `SetCredential=status-checkin:\n`: without that
  fallback a missing file fails the unit (243). An empty key or an empty
  `STATUS_MONITOR_ID` skips the check-in, logged.

The weekly a11y sweep keeps its `a11y-regression` issue as well: that is a work
item, not a page. `ready` and `egress-ip` no longer open issues.

## The API's OnFailure= dispatch

`power-map.service` carries
`OnFailure=power-map-notify-failure@%n.service` (CannObserv/notifier#114). The
handler logs a crit line tagged `power-map-unit-failure` first, then runs
`scripts/notify_unit_failure.py`: one dispatch per failed invocation
(`idempotency_key = {unit}:{invocation_id}`), variables `unit`, `result`, `host`
and `journal_cmd`, refused unless Notifier's `/health` says `production`.
`RestartMode=direct` keeps each restart out of `failed`, so the handler fires
once, when 10 starts land inside 600 s (a refused `ExecStartPre` schema apply
included) and systemd gives up. The service then stays down until
`sudo systemctl reset-failed power-map && sudo systemctl start power-map`. The
stored template's wording is `scripts/notifier/unit-failure.json`.

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
sudo journalctl -t power-map-unit-failure               # the handler's fallback line
```

