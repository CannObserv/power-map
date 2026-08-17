# power-map — Database Unreachable (incident triage)

Incident-time reading for a `pool_timeout` on `/ready` and the other readiness
failure reasons. Recurring data operations are in `docs/RUNBOOKS.md`, the
recurring audits in `docs/AUDITS.md`, and the planned-cutover checklist in
`docs/RUNBOOK_DB_MIGRATION.md`.

---

## Database unreachable — triage (`pool_timeout`)

The 2026-08-09 outage: the VM's NAT egress IP rotated `67.213.124.9` →
`69.67.149.183` with no VM-side change, and DO's Trusted Sources still held the
old address. Every DB-backed route 500'd for ~35 minutes. Nothing on the VM had
changed — no network events in the journal, no config edit, no terraform run.

### Identify

Run the whole column; the combination is what names the failure, not any one row.

| Probe | Reading that means "remote source-IP gate" |
|---|---|
| `curl -s localhost:8000/health` | **200** — the process is fine, so `Restart=on-failure` will never fire |
| `curl -s localhost:8000/ready` | **503 `pool_timeout`** — the reason slug is the triage |
| `journalctl -u power-map` traceback | asyncpg dies in `sock_connect` → `TimeoutError` — **connect**, not query |
| `ping <cluster host>` | **replies** — the host is up and routable |
| `bash -c 'cat </dev/null >/dev/tcp/<host>/25060'` | **hangs, no RST** — and so do 25061 and 443 |
| `getent hosts <host>` vs a public resolver | **identical** — the record is not stale |
| `sudo iptables -S` / `sudo ufw status` | policies `ACCEPT` / inactive — nothing local is dropping |

ICMP answered while TCP is silently dropped on *every* port is the signature.
A closed port on an allowlisted host answers with an RST; a source-IP gate
answers with nothing.

### Fix

```bash
curl -s https://api.ipify.org        # the address to allowlist
```

Add it in DO → Databases → `co-pm-db-1` → Settings → **Trusted Sources**. The
pool reconnects on its own — **no restart needed**. Confirm:

```bash
curl -fsS localhost:8000/ready       # {"status":"ok"}
```

Then close the loop so it does not silently recur:

1. Nothing to update for the guard — since #409 `power-map-egress-ip` reads the
   live Trusted Sources from the DO API, so the console edit *is* what it checks.
   (`EGRESS_EXPECTED_IPS` is only the no-token fallback.)
2. Re-sync terraform so the next `apply` does not revert the console edit:
   ```bash
   uv run python -m scripts.write_terraform_credentials   # re-reads the live allowlist
   terraform -chdir=infra/terraform plan                  # expect: No changes
   ```

### Other reasons on `/ready`

| Slug | Meaning | First move |
|---|---|---|
| `pool_timeout` | pool acquire or probe query timed out | the table above |
| `no_pool` | lifespan never built the pool | `journalctl -u power-map` around the last restart |
| `db_error` | the query raised | the logged exception carries the detail; `/ready` deliberately does not |
| `unreachable` (guard-side) | nothing listening on :8000 | `systemctl status power-map` |

### Scheduled guards

`power-map-ready.timer` (every 2 min, #347) catches the effect; the
`ready-regression` GitHub issue carries the slug. `power-map-egress-ip.timer`
(every 5 min, #410) catches this specific cause and hands over the new address.
Install / update either:

```bash
sudo cp infra/power-map-ready.service infra/power-map-ready.timer /etc/systemd/system/
sudo cp infra/power-map-egress-ip.service infra/power-map-egress-ip.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now power-map-ready.timer power-map-egress-ip.timer

# Verify the alert path end to end (opens then closes a real issue)
READY_CHECK_FORCE_FAIL=1 uv run python -m scripts.check_ready
uv run python -m scripts.check_ready
```
