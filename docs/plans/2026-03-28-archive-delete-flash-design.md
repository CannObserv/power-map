# Flash notifications for archive and hard-delete — Design

**Issue:** #35
**Date:** 2026-03-28

## Goal

Add user feedback for org archive and hard-delete actions where the existing
`HX-Trigger` flash mechanism cannot be used (archive is a plain-form POST that
redirects; delete navigates away to a different page).

## Approved approach

### Archive (`POST /{org_id}/archive/`) — no flash

No change. The org detail page reloads showing the "Archived" badge, Restore,
and Delete buttons — the outcome is visually self-evident. Adding a toast here
is noise.

### Delete (`DELETE /{org_id}/`) — query-param flash + fix navigation

Two problems to solve together:

1. **Navigation bug:** current template uses `hx-target="body"` + `hx-push-url`
   with an empty `HTMLResponse`. HTMX replaces `<body>` with empty content
   then updates the URL — user sees a blank page. Fix: remove `hx-push-url`
   from the template and instead return `HX-Location: /admin/orgs/?flash=deleted`
   from the route, which triggers a proper HTMX page fetch.

2. **Flash:** use a pre-defined message key in the query string — `?flash=deleted`.
   The org list route reads the param, maps it to a fixed `(level, message)` pair,
   and passes it to the template. Template renders via the existing `message()`
   macro from `macros/flash.html`. No DB-derived content in the URL.

## Key decisions

- **Pre-defined keys only** (`deleted`, potentially others later) — no user/DB
  content in query params, avoids XSS and URL-encoding complexity.
- **`message()` macro** (server-rendered) rather than `flash_trigger` —
  appropriate for full-page renders where HTMX event dispatch isn't available.
- **`HX-Location` over `hx-push-url`** — proper HTMX navigation that fetches
  the destination page; eliminates the blank-page bug as a side effect.
- **No flash on archive** — state change is self-evident; avoids over-engineering.
- **No session infrastructure** — stateless query-param approach fits the
  project's auth model (exe.dev proxy, no server sessions).

## Out of scope

- Unarchive flash (same reasoning as archive — self-evident on page reload).
- Other entities (people, roles) — same pattern applies but tracked separately.
- URL-stripping the `?flash` param after render (acceptable for admin-only tool).

## Implementation

1. **Route (`orgs.py`):** `org_delete` — remove org name fetch (not needed for
   pre-defined key); after successful delete return
   `Response(status_code=204, headers={"HX-Location": "/admin/orgs/?flash=deleted"})`.
2. **Route (`orgs.py`):** `org_list` — read `flash: str | None = Query(None)`,
   map to `(level, body)` dict, pass as `flash_msg` to template.
3. **Template (`orgs/list.html`):** render `{{ message(flash_msg.level, flash_msg.body) }}`
   in the flash block when `flash_msg` is set.
4. **Template (`orgs/detail.html`):** remove `hx-push-url` from delete button
   (navigation now driven by `HX-Location` header).
5. **Tests:** integration tests for delete route asserting `HX-Location` header
   and `?flash=deleted` param; list route asserting `flash_msg` in context when
   param present.
