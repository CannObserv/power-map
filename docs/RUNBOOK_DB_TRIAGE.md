# power-map — Database Unreachable (incident triage)

Incident-time reading for a `pool_timeout` on `/ready` and the other readiness
failure reasons, and the connection boundary behind them: TLS, the CA, the three
roles and credential rotation (§ Connection boundary). Recurring data operations are in `docs/RUNBOOKS.md`, the
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

A TLS failure (`SSLCertVerificationError`, or `FileNotFoundError` naming the `sslrootcert`
path) reads as `db_error` on a running service. On a restart it is worse: `ExecStartPre`'s
`apply-schema.sh` connects first and fails, so :8000 never comes up (`unreachable`). First
move: § Connection boundary → *TLS failures*.

A `gaierror` / `Temporary failure in name resolution` in the traceback, with `getent hosts
<cluster host>` failing too, is not the DB: since #568 `tailscaled` answers every DNS lookup
(MagicDNS). `systemctl status tailscaled`, then `tailscale debug prefs | grep CorpDNS` (must be
true). Last resort while it is down: `sudo cp /etc/resolv.pre-tailscale-backup.conf
/etc/resolv.conf`, then `sudo systemctl restart tailscaled` once it is healthy to hand DNS back.

### Scheduled guards

`power-map-ready.timer` (every 2 min, #347) catches the effect and alerts
`not-ready` through co-status (#566); the journal carries the slug. Both guards'
cadence, hatches and overrides are in [AUDITS.md](AUDITS.md). `power-map-egress-ip.timer`
(every 5 min, #410) catches this specific cause and names the new address in its journal.
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

---

## Connection boundary — TLS and credentials (#667)

**Trusted Sources is not a per-VM boundary.** It admits power-map's exe.dev egress IP, and
exe.dev's outbound NAT is shared between VMs at least sometimes (two fresh VMs on one account
measured the same address, 2026-10-15). Any VM leaving through that address passes the
allowlist. What stands between it and the data is the credentials and TLS, so an audit or
threat model counts those, not the allowlist. The same holds for a new VM admitted at a
migration: its address may be shared too.

### Target state

- **Every DSN is `sslmode=verify-full`** against the cluster CA: the client checks the
  server's certificate chain *and* its hostname, so a man-in-the-middle cannot present
  another certificate. All three DSNs in `/etc/power-map/.env` carry
  `?sslmode=verify-full&sslrootcert=/etc/power-map/do-ca.crt`. No code sets TLS options;
  asyncpg (pool, `apply-schema.sh`, every `scripts/` DSN) and libpq (`psql`) both read
  them from the DSN.
- **The CA** is `/etc/power-map/do-ca.crt`, `0644 root:root`. It isn't a secret, and every
  unit runs as `exedev`. It is the DO *project* CA, so it is never committed: it comes from
  terraform (`data "digitalocean_database_ca"` → output `db_ca_certificate`), and
  `scripts/write-db-secrets.sh` installs it before it connects anywhere. The server
  refuses unencrypted connections (`pg_hba`: "no encryption").
- **Three roles, split by design** (`infra/terraform/database.tf`, grants in
  `write-db-secrets.sh`): `DATABASE_URL`'s app user has DML only, through the migrations
  user's default privileges; `MIGRATIONS_DATABASE_URL`'s user owns the schema and is the
  only DSN `apply-schema.sh` uses; `TEST_DATABASE_URL`'s user owns the test DB. None is
  superuser or holds `CREATEDB` / `CREATEROLE`. Any change to grants is the operator's call.
- `tests/scripts/test_write_db_secrets.py` holds the writer to this, and fails if any
  `sslmode=` outside `tests/` asks for less than `verify-full`.

### Proving a DSN change

Never edit `/etc/power-map/.env` on faith. A wrong CA path or mode takes the service down
at the next restart (§ Other reasons).

1. Prove the new DSN with a one-off **read-only** connection
   (`server_settings={"default_transaction_read_only": "on"}`), test DB first, printing no
   DSN.
2. Change `TEST_DATABASE_URL`, then run the integration tier **alone**.
3. Edit the prod lines, keeping each old line commented above its replacement for rollback.
4. Restart in a quiet window, not 00:00–00:06 or 09:00–09:35 UTC. Check `curl -fsS localhost:8000/ready`.
5. Rollback: swap the commented lines back and restart. Once `/ready` has held, delete the
   commented lines: they carry credentials, and `write-db-secrets.sh` keeps every `#` line.

### TLS failures

```bash
sudo test -r /etc/power-map/do-ca.crt && ls -l /etc/power-map/do-ca.crt   # 0644 root:root
openssl x509 -in /etc/power-map/do-ca.crt -noout -subject -enddate        # "Project CA", not expired
# the two fingerprints must match
terraform -chdir=infra/terraform output -raw db_ca_certificate | openssl x509 -noout -fingerprint -sha256
openssl x509 -in /etc/power-map/do-ca.crt -noout -fingerprint -sha256
```

- **Missing or stale CA:** `terraform -chdir=infra/terraform apply -refresh-only` (a state
  write), then `bash scripts/write-db-secrets.sh`. It refuses (exit 2) if the output isn't a
  PEM certificate.
- **Hostname mismatch:** the DSN's host must be one the server certificate names. The
  cluster's public hostname is; a hand-typed alias or a resolver override is not.

### Rotating a credential

The operator decides when. Pick a quiet window: from the reset until the env file changes,
new connections with the old password fail. Pooled connections survive until they are
recycled, but the timers that start in that gap fail.

1. Reset the user's password: DO console → Databases → `co-pm-db-1` → *Users & Databases*,
   or the API's `POST /v2/databases/{id}/users/{name}/reset_auth`. **Never**
   `terraform apply -replace` a `digitalocean_database_user`: recreating the migrations
   user would orphan the objects it owns, and the app user would lose its grants.
2. `terraform -chdir=infra/terraform apply -refresh-only` so the state, and so the
   outputs, hold the new password. The first time, check that the output changed.
3. `bash scripts/write-db-secrets.sh`. It rewrites all three DSNs (verify-full), reinstalls
   the CA, re-applies the idempotent grants and smoke-tests each DSN.
4. App or migrations user: `sudo systemctl restart power-map`, then
   `curl -fsS localhost:8000/ready`. The test user needs no restart. Timers read the env
   file on each run.
5. Delete any commented DSN lines that still carry the old password; the writer keeps them.
