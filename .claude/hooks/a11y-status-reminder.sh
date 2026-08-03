#!/usr/bin/env bash
# SessionStart note (GH #369): surface the weekly a11y sweep status when a Claude
# session opens on the VM. Silent when healthy. No-ops off the VM or before the
# timer is installed — it only queries systemd, holds no state.
set -uo pipefail

UNIT="power-map-a11y.service"

# Off-VM / no systemd, or unit not installed yet → say nothing.
command -v systemctl >/dev/null 2>&1 || exit 0
systemctl cat "$UNIT" >/dev/null 2>&1 || exit 0

state="$(systemctl is-failed "$UNIT" 2>/dev/null || true)"
if [ "$state" = "failed" ]; then
    echo "⚠️  Weekly a11y sweep (power-map-a11y) is FAILED. Investigate:" \
        "\`journalctl -u power-map-a11y -n 100\` and the open \`a11y-regression\` GitHub issue."
    exit 0
fi

# Not failed: check for staleness (weekly cadence → warn past 8 days, or if it has
# never run). ExecMainStartTimestamp is the last run regardless of outcome; since
# we already returned on failure, reaching here means the last run was fine.
last="$(systemctl show "$UNIT" -p ExecMainStartTimestamp --value 2>/dev/null || true)"
if [ -z "$last" ]; then
    echo "ℹ️  Weekly a11y sweep (power-map-a11y) has not run yet — first run Sundays 04:00 UTC," \
        "or trigger now: \`sudo systemctl start power-map-a11y.service\`."
    exit 0
fi

last_epoch="$(date -d "$last" +%s 2>/dev/null || echo 0)"
now_epoch="$(date +%s)"
if [ "$last_epoch" -gt 0 ] && [ $((now_epoch - last_epoch)) -gt $((8 * 24 * 3600)) ]; then
    echo "ℹ️  Weekly a11y sweep (power-map-a11y) last ran $last — over 8 days ago" \
        "(timer may be disabled). Check: \`systemctl list-timers power-map-a11y.timer\`."
fi
exit 0
