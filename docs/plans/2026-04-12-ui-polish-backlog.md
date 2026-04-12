# UI Polish Backlog — Issues #88, #90, #91, #92, #93, #94

Date: 2026-04-12

## Goal

Clear six open UI polish issues against the admin dashboard: one backend correctness bug (missing redirect after role deletion), three CSS layout fixes, one template cleanup, and one string rename. All issues are pre-production, self-contained, and unblocked.

## Approved approach

Six sequential agents, each in an isolated worktree. Ordered by score (correctness first, then CSS cluster, then mechanical). Sequential execution eliminates all merge conflict risk on the shared `admin.css` file.

## Prioritization rubrics

**Score = (Foundation × 2) + (Correctness × 2) + Scope** — max 15

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| Foundation Leverage | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| Correctness Risk | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| Scope Clarity | Requires design discovery | Clear direction, minor decisions needed | Mechanical — implementation is obvious |

Blast radius drives sequencing, not score. Deployment context: pre-production.

## Scored backlog

| # | Title | F | C | S | Score | Blast |
|---|---|---|---|---|---|---|
| #91 | After deleting a Role, browser not redirected | 1 | 3 | 2 | **10** | Low — `roles.py` + test |
| #94 | Assignment table action buttons stacking | 1 | 2 | 3 | **9** | Med — `admin.css` + `_assignment_row.html` |
| #90 | Min-width for date columns, all list screens | 2 | 1 | 2 | **8** | Med — `admin.css` + all `_region.html` |
| #88 | Remove top pagination bar from Roles list | 1 | 1 | 3 | **7** | Low — `roles/_region.html` only |
| #92 | Breadcrumb vertical alignment in header | 1 | 1 | 3 | **7** | Low — `admin.css` only |
| #93 | HTML title: "power-map" → "Power Map" | 1 | 1 | 3 | **7** | Low — `base.html` only |

## Conflict zones

| File | Issues | Notes |
|---|---|---|
| `src/static/admin/admin.css` | #94, #90, #92 | All three touch CSS; sequential execution eliminates conflict risk |
| `src/api/admin/roles.py` | #91 | Sole owner; add HX-Redirect header to delete route |
| `src/templates/admin/roles/_region.html` | #88 | Sole owner; remove top pagination call |
| `src/templates/admin/roles/partials/_assignment_row.html` | #94 | Action cell needs `white-space: nowrap` or flex wrapper |
| `src/templates/admin/base.html` | #93 | Sole owner; string change in `<title>` |
| All list `_region.html` files | #90 | Roles, people, orgs, role_assignments — adds date-column class |

## Dependency graph

No hard logical dependencies between any issue. Sequencing is score-first with the `admin.css` cluster grouped consecutively.

```
#91 → #94 → #90 → #92 → #88 → #93
 ↑         [admin.css cluster]
correctness
```

## Batch execution plan

| Batch | Issue | Agent | Files | Gate |
|---|---|---|---|---|
| 1 | #91 — redirect after role delete | 1 (sequential) | `roles.py`, `tests/admin/test_roles.py` | Start immediately |
| 2 | #94 — assignment buttons stacking | 1 (sequential) | `admin.css`, `_assignment_row.html` | After 1 merged |
| 3 | #90 — date column min-width | 1 (sequential) | `admin.css`, all list `_region.html` | After 2 merged |
| 4 | #92 — breadcrumb vertical alignment | 1 (sequential) | `admin.css` | After 3 merged |
| 5 | #88 — remove top pagination bar | 1 (sequential) | `roles/_region.html` | After 4 merged |
| 6 | #93 — HTML title site name | 1 (sequential) | `base.html` | After 5 merged |

Each agent: worktree isolation → TDD where applicable → all tests pass → lint clean → self-review → signal completion.

## Key decisions

- **#91 leads**: only correctness issue (score 10); user sees blank screen after delete — silent navigation failure. Backend-only change, isolated from CSS work.
- **#94 before #90**: both touch `admin.css`; button-stacking fix is higher priority and its selector scope is narrow (assignment row actions), so #90's broader `.data-table` column rules won't interfere.
- **#88 near end**: template-only, zero dependencies, low risk — placed after CSS cluster so reviewer sees grouped CSS diffs first.
- **#93 last**: single string in `base.html`, zero risk, zero dependencies.
- **Sequential over parallel**: user preference; also simplifies worktree management and allows each diff to be reviewed in isolation before the next starts.

## Deferred items

None — all six issues are in scope.

## Out of scope

- #89 (Disable Merge button when fewer than 2 roles) — already closed/merged prior to this session.
- No architectural changes; all work is additive CSS/template/route fixes.
