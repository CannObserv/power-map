# Runbook: Local PostgreSQL → DO Managed Database Cutover

Migration of the production database from the local VM PostgreSQL instance to
DigitalOcean Managed PostgreSQL (`co-pm-db-1`, sfo3). The full provisioning
and sync steps are in `docs/COMMANDS.md § Provisioning`; this runbook covers
the final cutover window, rollback path, and post-cutover validation.

---

## Pre-cutover checklist

Complete all items before entering the maintenance window.

- [ ] `terraform apply` completed — cluster, databases, users, and firewall all
      exist in DO (`terraform -chdir=infra/terraform show` confirms 8 resources)
- [ ] `bash scripts/write-db-secrets.sh` completed — `/etc/power-map/.env`
      contains `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`;
      smoke-test passed (both DSNs return `SELECT 1`)
- [ ] `bash scripts/sync-schema-to-do.sh` completed — extensions installed on
      both DO databases; `co_pm_db_test` schema applied
- [ ] `bash scripts/sync-data-to-do.sh` completed — row counts match local;
      no mismatches reported
- [ ] Local postgres is still running (needed for final sync during window)
- [ ] Know the local database DSN or confirm peer auth works:
      `sudo -u postgres psql -d powermap -c "SELECT COUNT(*) FROM people;"`
- [ ] Have rollback path ready (see § Rollback below)

---

## Maintenance window

Estimated duration: **~5 minutes** (dominated by final sync; service downtime
starts at step 1 and ends at step 4).

### Step 1 — Stop the production service

```bash
sudo systemctl stop power-map
sudo systemctl status power-map   # confirm: inactive (dead)
```

### Step 2 — Final data sync

Run a final sync to capture any writes that occurred between the pre-cutover
sync and the service stop.

```bash
bash scripts/sync-data-to-do.sh
# If the pre-cutover sync ran with a LOCAL_DSN argument, pass it again here.
```

Confirm: "All row counts match — ready for cutover" printed with no mismatches.

### Step 3 — Update and reload the service unit

The updated `power-map.service` removes the `After=postgresql.service`
dependency. Install it now:

```bash
sudo cp infra/power-map.service /etc/systemd/system/power-map.service
sudo systemctl daemon-reload
```

### Step 4 — Start the service against DO

`/etc/power-map/.env` already points `DATABASE_URL` at DO (written by
`write-db-secrets.sh`). Simply start:

```bash
sudo systemctl start power-map
sudo systemctl status power-map   # confirm: active (running)
sudo journalctl -u power-map -n 50 --no-pager
```

Confirm: no database connection errors in the journal.

### Step 5 — Post-cutover smoke tests

```bash
# Health endpoint
curl -sf https://power-map.exe.xyz:8000/health | python3 -m json.tool

# Spot-check a few API routes (replace KEY with a valid API key)
curl -sf -H "X-API-Key: KEY" https://power-map.exe.xyz:8000/v1/people?limit=5 \
    | python3 -m json.tool

# Admin dashboard — confirm it loads and data is visible
# Open https://power-map.exe.xyz:8000/admin/ in a browser
```

### Step 6 — Stop and disable local PostgreSQL

Only after smoke tests pass:

```bash
sudo systemctl stop postgresql
sudo systemctl disable postgresql
sudo systemctl status postgresql   # confirm: inactive (dead) + disabled
```

Optionally remove the local data directory to reclaim disk (irreversible):

```bash
# WARNING: only run after confirming cutover is stable
sudo pg_dropcluster --stop 16 main
```

### Step 7 — Seed lookup tables (if not already seeded)

BCP 47 / ISO 15924 tables must be populated before any write to
`person_names.locale` / `.script`. Check and seed if empty:

```bash
env_args=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)
uv run "${env_args[@]}" --group seed scripts/seed_locales_scripts.py
```

---

## Rollback

If smoke tests fail or the service cannot start against DO:

```bash
# 1. Stop the service
sudo systemctl stop power-map

# 2. Re-point DATABASE_URL at local postgres
#    Edit /etc/power-map/.env and replace DATABASE_URL with the local DSN:
#    DATABASE_URL=postgresql://powermap:<password>@localhost/powermap
sudo nano /etc/power-map/.env

# 3. Ensure local postgres is running
sudo systemctl start postgresql

# 4. Restore the old service unit (if already updated in step 3 of cutover)
#    The old unit had After=postgresql.service — restore it from git if needed:
git show main:infra/power-map.service | sudo tee /etc/systemd/system/power-map.service
sudo systemctl daemon-reload

# 5. Start the service
sudo systemctl start power-map
sudo systemctl status power-map

# 6. Verify
curl -sf https://power-map.exe.xyz:8000/health
```

The DO cluster remains intact during rollback — no data is lost on either side.
Once the root cause is resolved, re-enter the maintenance window from Step 2.

---

## Post-cutover validation checklist

- [ ] `sudo systemctl status power-map` — active (running)
- [ ] `/health` endpoint returns 200
- [ ] Admin dashboard loads with correct data
- [ ] DO Control Panel: `co-pm-db-1` dashboard shows active connections
- [ ] `sudo systemctl is-enabled postgresql` — disabled
- [ ] Integration tests pass against DO test DB:
      `uv run pytest -m integration --no-cov -q`
- [ ] COMMANDS.md § Service Management note updated: remove reference to
      `postgresql.service` from any operator notes if present
