# Architectural Refactor Backlog — power-map (issues #77–#86)

**Date:** 2026-04-11
**Scope:** 10 issues from the 2026-04-11 architectural review. All are refactors and one targeted fix — no feature work.

---

## Goal

Clear the architectural debt identified in the 2026-04-11 AR: dead pool lifecycle code, duplicated initialisation logic, oversized modules, mirrored subsection pairs, and pervasive boilerplate. Execution uses a 4-batch hybrid plan with parallel agents within batches and merge gates between batches.

---

## Approved Approach

- **Pre-production** — build it right, no stability constraints
- **Quality:** testability, correctness, and maintainability weighted equally
- **Parallelism:** hybrid — maximise parallel agents within each batch; sequential gates between batches
- **Isolation:** git worktrees (`isolation: "worktree"`) for all agents
- **Merge strategy:** regular merge commits to main (preserves per-agent commit history)
- **Batch branches:** `batch/a`, `batch/b`, `batch/c`, `batch/d` — orchestrator checks out batch branch before spawning agents so worker output accumulates there

---

## Prioritisation Rubrics

**Score = (Foundation × 2) + (Correctness × 2) + Scope** — max 15

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| **Correctness Risk** | Cosmetic / organisational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions needed | Mechanical — implementation obvious from issue |

Foundation and Correctness are doubled to weight architectural safety and foundational concerns over mechanical effort. Blast radius drives *sequencing*, not score.

---

## Scored Backlog

| # | Title | F | C | S | Score | Blast |
|---|---|---|---|---|---|---|
| #84 | Log suppressed dup-count exceptions | 1 | 3 | 3 | **11** | Low |
| #77 | Wire `main.py` to `db.py` pool lifecycle | 2 | 2 | 3 | **11** | Med |
| #81 | Eliminate `check_auth` boilerplate | 3 | 1 | 2 | **10** | High |
| #78 | Consolidate address normaliser singleton | 1 | 2 | 3 | **9** | Med |
| #80 | Eliminate mirrored module pairs | 1 | 2 | 1 | **7** | High |
| #82 | Split `settings.py` | 1 | 1 | 3 | **7** | Med |
| #83 | Split `roles_detail.py` | 1 | 1 | 3 | **7** | Med |
| #85 | Extract dashboard from `router.py` | 1 | 1 | 3 | **7** | Med |
| #86 | Document stale dup-count cache | 1 | 1 | 3 | **7** | Low |
| #79 | Split `orgs.py` / `people.py` | 1 | 1 | 2 | **6** | Med |

**Score rationale:**
- **#84 C=3**: silent failure — broken `pg_trgm` or connectivity shows as "0 duplicates" with no log signal
- **#77 F=2**: `deps.get_db` and `router.py` dashboard handler both benefit; enables #85's clean extraction
- **#81 F=3**: ~60 route handlers simplified — widest positive impact across the codebase; **S=2** because FastAPI redirect at dep layer requires verification
- **#80 S=1**: design discovery needed — factory vs shared base vs code-gen; names pair has complex parameterisation (canonical guard, per-entity header events)

---

## Conflict Zones

Files touched by 2+ issues:

| File | Issues | Required merge order | Rationale |
|---|---|---|---|
| `src/api/admin/router.py` | #77, #85, #79, #82, #83 | #77 → #85 → #79/#82/#83 (any) | #77 modifies dashboard handler; #85 extracts it; remainder are append-only mounts |
| `src/api/admin/deps.py` | #77, #81 | #77 → #81 | Different functions (`get_db` vs `get_admin_user`/`check_auth`); same file |
| `src/api/admin/org_dups.py` | #84, #86 | bundled (one agent) | Tiny changes; bundling eliminates conflict at zero cost |
| `src/api/admin/people_dups.py` | #84, #86 | bundled (one agent) | Same |
| `src/api/admin/orgs.py` | #79, #81 | #79 → #81 | #79 removes ~150 lines; #81 cleans remaining handlers |
| `src/api/admin/people.py` | #79, #81 | #79 → #81 | Same |
| `src/api/admin/settings.py` | #82, #81 | #82 → #81 | #82 slims file; #81 cleans remaining handlers |
| `src/api/admin/roles_detail.py` | #83, #81 | #83 → #81 | Same |
| 8 mirrored modules | #81, #80 | #81 → #80 | #80 refactors cleaner code after boilerplate removed |

---

## Dependency Graph

```
#84+#86 ──┐
#78 ──────┤  Batch A — 3 parallel agents (fully disjoint files)
#77 ──────┘
              │
              ▼  [gate: Batch A merged]
#85 ──┐
#79 ──┤  Batch B — 4 parallel agents (sequential merge into batch/b)
#82 ──┤           merge order: B1(#85) first, then B2/B3/B4 any order
#83 ──┘
              │
              ▼  [gate: Batch B merged]
#81 ─── Batch C — 1 agent (sweeps all route modules including Batch B new files)
              │
              ▼  [gate: Batch C merged]
#80 ─── Batch D — 1 agent (mirrored pair factory refactor on clean base)
```

---

## Batch Execution Plan

### Batch A — 3 parallel agents, start immediately

| Agent | Issues | Primary files |
|---|---|---|
| A1 | #84 + #86 (bundled) | `org_dups.py`, `people_dups.py` |
| A2 | #78 | `normalizers/address.py`, `orgs_addresses.py`, `people_addresses.py`, `pipeline.py` |
| A3 | #77 | `main.py`, `db.py`, `deps.py`, `router.py` (dashboard handler section) |

All three have fully disjoint file coverage — merge to `batch/a` in any order.

### Batch B — 4 parallel agents, gate: Batch A merged

| Agent | Issue | Primary files |
|---|---|---|
| B1 | #85 | `router.py` (remove dashboard handler), new `dashboard.py` |
| B2 | #79 | `orgs.py`, `people.py`, new `orgs_merge.py`, `people_merge.py`, `router.py` (append) |
| B3 | #82 | `settings.py`, new `settings_link_types.py`, `settings_identifier_types.py`, `router.py` (append) |
| B4 | #83 | `roles_detail.py`, new `roles_assignments_inline.py`, `router.py` (append) |

All four work in parallel worktrees. All touch `router.py` — orchestrator merges to `batch/b` in this order: **B1 first** (removes dashboard section #77 already updated), then B2/B3/B4 in any order (append-only mount additions).

### Batch C — 1 agent, gate: Batch B merged

| Agent | Issue | Primary files |
|---|---|---|
| C1 | #81 | `deps.py` + all ~23 route modules (including Batch B new files) |

Single sweep removing `check_auth` boilerplate. Runs after all module splits so new files (`orgs_merge.py`, `settings_link_types.py`, etc.) are also cleaned in the same pass. Change to `get_admin_user` in `deps.py` must be verified: FastAPI must issue the 307 redirect at the dependency layer with the same `/__exe.dev/login?redirect=...` target as the current `check_auth` path.

### Batch D — 1 agent, gate: Batch C merged

| Agent | Issue | Primary files |
|---|---|---|
| D1 | #80 | 8 mirrored modules + new shared base(s) |

Most complex issue. Agent must first decide parameterisation strategy (see Key Decisions below) before writing any implementation. All existing tests must pass; parity tests should be added to catch future divergence.

---

## Key Decisions

**#84+#86 bundled into A1.** Both touch only `org_dups.py` and `people_dups.py`. Bundling eliminates a file conflict for zero additional complexity — the changes are additive and independent within each file.

**#77 leads Batch A (not just a peer).** `router.py`'s dashboard handler must be updated before #85 extracts it in Batch B. Running #77 in Batch A ensures Batch B agents always base on the updated pool-access pattern.

**#81 is Batch C, not Batch B.** #81 touches all route modules including files that Batch B *creates* (`orgs_merge.py`, `people_merge.py`, `settings_link_types.py`, `settings_identifier_types.py`, `roles_assignments_inline.py`). Running #81 after Batch B means one clean sweep covers old and new files — no double-touching.

**#80 is Batch D, alone.** Depends on #81 (cleaner module base) and is the only issue with `S=1` (requires design discovery). Isolating it prevents its uncertainty from blocking other work.

**#80 parameterisation — agent must choose before coding.** The names pair has entity-specific behaviour (`_maybe_promote_sole_name`, canonical edit guard, `updateOrgHeader` vs `updatePersonHeader` header events). A factory function (`_name_crud_router(entity_type, table, pk_col, header_event, promote_fn)`) is the recommended approach — it avoids inheritance complexity while making parameters explicit. The agent should validate this on one pair before applying to all four.

---

## Deferred Items

None — all 10 issues are in scope.

---

## Out of Scope

- Public REST API (#8 from AR was stet'd)
- Any new features
- DB schema changes
