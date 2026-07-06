---
title: "#275 Phase 1 — Jurisdictions admin: browse & inspect"
date: 2026-07-06
status: draft
---

# #275 Phase 1 — Jurisdictions admin: browse & inspect

Design: `docs/plans/2026-07-06-jurisdictions-admin-surface-design.md` · Issue: #275
Branch: `feature/275-jurisdictions-admin-phase-1`

## Problem

Jurisdictions are a fully-realized backend entity with a complete public read
API, but the admin dashboard surfaces them only as a `/search/` typeahead feeding
the role form. They have no nav entry, no dashboard count, no list, and no detail
view — an operator cannot browse or inspect a jurisdiction, its graph edges, its
lineage, or which roles/orgs reference it. Phase 1 makes the entity **visible and
inspectable** (read-only); create/edit (Phase 2) and graph/affiliation editing
(Phase 3) follow.

## Approach

Mirror the orgs admin module, read-only. Add a `query_jurisdictions_rows` builder
(like `orgs_queries.py`, but ILIKE search on name/slug since `jurisdictions` has
no `search_tsv`, plus a type filter and a three-value status axis
active/archived/superseded). Extend `jurisdictions.py` with a list route (HTMX
region swap) and a read-only detail route whose panels reuse the public API's SQL
shapes for relationships and lineage. Wire the nav link, Entities card, and
dashboard count. No writes, no schema changes. TDD throughout, mirroring
`tests/api/admin/test_orgs.py` (integration, `db_pool`, `loop_scope="session"`,
`AUTH_HEADERS`).

## Tradeoffs / alternatives

- **Full-text search via a new `search_tsv` on jurisdictions** — rejected;
  out of scope (schema change) and unwarranted for a small, curated table. ILIKE
  on name/slug matches the existing typeahead and is sufficient.
- **Build interactive attachment panels now** — rejected; Phase 1 is read-only.
  Panels render simple read lists; Phase 2 swaps in the factory-router CRUD
  partials. Small, accepted rework over premature coupling.
- **Defer lineage to Phase 3 (graph)** — rejected; lineage is read-only display
  and the recursive CTE already exists in `src/api/public/jurisdictions.py` to
  copy. It belongs with the read surface.

## Steps

1. **Query builder** — RED: `tests/api/admin/test_jurisdictions_queries.py` for
   `query_jurisdictions_rows(db, q, status, type_slug, page, page_size)` covering
   search, type filter, status axis (active/archived/superseded), and pagination
   shape. GREEN: add `src/api/admin/jurisdictions_queries.py` (ILIKE search,
   `pagination_context`). Verify: new test passes.
2. **List route + templates** — RED: `test_jurisdictions.py::test_list_*` (200,
   unauth redirect, search/type/status render, HTMX `_region` swap). GREEN: add
   `jurisdictions_list` to `jurisdictions.py`; `templates/admin/jurisdictions/`
   `list.html` + `_region.html` (mirror orgs); `active_section='jurisdictions'`.
   Verify: list tests pass; manual `GET /admin/jurisdictions/`.
3. **Nav + Entities card + dashboard count** — RED: tests that the sidebar link,
   the Entities-index card, and the dashboard count render/query. GREEN: add the
   `<a>` in `base.html`; a card in `entities/index.html` (no dup badge); add
   `jurisdictions` to the `dashboard.py` counts query (+ the `entities` route
   count context if it has one). Verify: tests pass.
4. **Detail route + header** — RED: `test_jurisdiction_detail_*` (200 renders
   name/slug/type/validity/superseded/notes; 404 unknown id). GREEN: add
   `jurisdiction_detail` to `jurisdictions.py` + `detail.html` header (reuse
   `v_jurisdiction_display_names`). Verify: header tests pass.
5. **Detail attachment panels (read-only)** — RED: per-panel tests that seeded
   identifiers / links / addresses / contacts render. GREEN: add the four fetches
   to `jurisdiction_detail` + read-only panels in `detail.html`. Verify: panel
   tests pass.
6. **Detail graph panels (read-only)** — RED: tests that relationships (both
   directions, with type/category/validity), lineage chain, affiliated orgs, and
   referencing roles (`roles.jurisdiction_id`, archived excluded) render. GREEN:
   add the fetches (copy the relationships + recursive-lineage SQL from
   `src/api/public/jurisdictions.py`) + panels. Verify: graph-panel tests pass.
7. **Docs + full verification** — update `docs/STYLE.md §32` (admin conventions)
   and the AGENTS.md admin note alongside the code. Run `ruff` + the full pytest
   suite; smoke-test on the dev server (port 8001 from the worktree). Verify:
   green suite, clean lint, page renders end-to-end.

## Open questions / risks

- **Detail page query count** — the detail view fires ~8 queries (header + 4
  attachments + relationships + lineage + affiliations + roles). Acceptable for a
  low-traffic admin page; flag if it feels heavy and batch later.
- **Lineage CTE reuse** — copying the recursive CTE from the public module risks
  drift between the two copies. Acceptable for Phase 1; a shared core helper could
  be extracted later if a third caller appears.
- **Superseded status lens** — confirm the desired semantics: `superseded_at IS
  NOT NULL` as a distinct filter value vs. a badge only. Plan assumes a distinct
  status filter value; easy to demote to a badge if preferred.
