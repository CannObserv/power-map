# Design: PostgreSQL Setup Script

**Date:** 2026-03-18
**Status:** Approved

## Goal

Add a `scripts/setup-db.sh` that provisions a local PostgreSQL instance on the exe.dev VM — installs postgres, creates roles/databases, applies schema, and writes connection vars to `env`.

## Approved Approach

Shell script (`scripts/setup-db.sh`), idempotent and safe to re-run. No external tooling (Terraform rejected as wrong abstraction for a single VM).

## Key Decisions

- **Two databases:** `powermap` (dev) and `powermap_test` (integration tests), matching the project's `DATABASE_URL` / `TEST_DATABASE_URL` convention
- **One postgres role:** `powermap` with a generated random password, owns both databases
- **Schema applied to both** via `src/core/schema.sql` at setup time
- **`env` file is additive:** script appends `DATABASE_URL` and `TEST_DATABASE_URL` only if not already present; existing values are never overwritten
- **Idempotent:** uses `CREATE ROLE IF NOT EXISTS`, `CREATE DATABASE` with existence check, `psql -c` for safety; re-running is a no-op if already configured

## Out of Scope

- Terraform / cloud provisioning — deferred to if/when a real deployment target exists
- Multi-environment management (staging, prod)
- Superuser role for dev convenience — standard `powermap` role is sufficient
