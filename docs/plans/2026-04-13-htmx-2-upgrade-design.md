# HTMX 2.x Upgrade + Self-Host — Design

**Issue:** #33
**Date:** 2026-04-13

## Goal

Replace the unpkg-hosted HTMX 1.9.12 dependency in `src/templates/admin/base.html` with a self-hosted, pinned copy of HTMX 2.0.8 served from `src/static/admin/vendor/`.

## Motivation

- Remove third-party runtime dependency (unpkg outage or compromise would break the admin dashboard).
- Pin to a specific version under our control — easier to audit, works in network-restricted environments.
- Get onto the current stable 2.x line while the migration surface is small.

## Approved approach

1. Download `htmx.min.js` v2.0.8 to `src/static/admin/vendor/htmx-2.0.8.min.js` (version in filename acts as cache-buster and makes the audited version obvious).
2. Swap the `<script>` tag in `src/templates/admin/base.html` from the unpkg URL to a `{{ url_for('static', path=...) }}` reference, keeping the `defer` attribute.
3. Smoke-test the admin flows that exercise HTMX surface area (sidebar boost nav, flash triggers, typeahead search, row-level edit swaps, delete modal, address confirm modal).
4. Ship as one commit: `#33 chore: upgrade htmx to 2.0.8 and self-host`.

## Why this is low-risk

- **`hx-on` syntax** — the main 1.x→2.x breaking change. All 5 call sites in the codebase already use the 2.x double-colon form (`hx-on::after-request`), so no template rewrites are needed.
- **Attribute surface** — only uses the stable core attributes (`hx-get/post/delete`, `hx-swap`, `hx-target`, `hx-boost`, `hx-trigger`, `hx-vals`, `hx-include`). All behave identically in 2.x.
- **No `hx-ext`** — no extensions to re-vendor (SSE, WebSocket, etc. were extracted as separate packages in 2.x, but we don't use them).
- **No cross-origin requests** — 2.x's `htmx.config.selfRequestsOnly = true` default is fine for an internal admin.
- **Default swap style** unchanged (`innerHTML`).
- **`htmx:` event names** unchanged; no legacy underscore names in use.
- **Browser support** — 2.x drops IE11; internal admin, not a concern.
- **`hx-boost` script re-execution** — unchanged in 2.x; existing `<head>`-script convention in `base.html` still applies.

## Bonus fix

Issue #33 notes that OOB swap scanning inside `<template>` was broken in 1.9.12 and fixed in 2.x. Nothing in the current code depends on the broken behavior, so this is a latent capability gain, not a migration risk.

## Smoke-test checklist (dev server on :8001)

- [ ] Boosted sidebar navigation between admin sections
- [ ] Flash trigger (`HX-Trigger: showFlash`) fires on a mutation (e.g. edit a person's pronouns)
- [ ] Typeahead search (e.g. person → add role assignment → org search)
- [ ] Row-level HTMX editing (inline name edit on an org)
- [ ] Delete modal `hx-on::after-request` callback
- [ ] Address confirm modal `hx-on::after-request` callback
- [ ] Auto-saving active toggle on an org
- [ ] Aria-busy announcements on a slow-ish request

## Out of scope

- Flash message redesign (#32).
- Adopting HTMX 2.x extensions.
- Any refactor of existing HTMX attributes — this is a drop-in version bump.

## Rollback

Revert the single commit. The previous CDN `<script>` tag is restored, and no template or JS changes accompany the bump, so there is nothing else to undo.
