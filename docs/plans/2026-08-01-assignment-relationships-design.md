# Design — Role-Assignment → Role-Assignment relationships (#301)

**Date:** 2026-08-01
**Issue:** #301 (follow-on to #266)
**Status:** Approved — ready for implementation

## Goal

Model the **person→person staff relationship** — a staffer serving a specific
principal legislator — that the flat role model cannot capture. Today the
principal survives only as free text baked into the role title
(e.g. `"Legislative Aide, Senator June Robinson"`). #266 reclassifies these to a
generic `legislative_staff` type, stripping the principal from the title and
leaving it homeless. This issue gives it a home.

Modeled as a **directional, temporal Role-Assignment → Role-Assignment edge**
(staffer's assignment → principal's seat assignment), NOT person→person — so the
object assignment context (org, role, title, window) is preserved on both sides.
Biennium turnover means frequent churn: each new term = new assignments on both
sides = a new edge.

## Approved approach

### Data model

**New catalog** `role_assignment_relationship_types` (mirrors
`jurisdiction_relationship_types`):

```
id TEXT PK, slug TEXT UNIQUE, display_name TEXT,
is_symmetric BOOLEAN DEFAULT FALSE, category TEXT, description TEXT
```

Seed one row: `staff_of` (`is_symmetric=FALSE`). Directional semantics:
**from = staffer, to = principal** ("from is *staff_of* to"). `is_symmetric` is
kept for catalog parity but no symmetric query paths are built (YAGNI —
`staff_of` is strictly directional).

**New edge** `role_assignment_relationships`:

```sql
id                 TEXT PK (ULID via generate_id())
from_assignment_id TEXT NOT NULL REFERENCES role_assignments(id) ON DELETE CASCADE
to_assignment_id   TEXT NOT NULL REFERENCES role_assignments(id) ON DELETE CASCADE
rel_type_id        TEXT NOT NULL REFERENCES role_assignment_relationship_types(id)
valid_from         DATE
valid_until        DATE
source_key_id      TEXT REFERENCES api_keys(id)   -- provenance, nullable (curator/backfill)
notes              TEXT
created_at / updated_at / archived_at
CONSTRAINT chk_no_self_rel CHECK (from_assignment_id <> to_assignment_id)
CONSTRAINT chk_edge_valid_range
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until)
```

Identity / refine-in-place key:
`UNIQUE (from_assignment_id, to_assignment_id, rel_type_id) WHERE archived_at IS NULL`
`NULLS NOT DISTINCT`. `ON DELETE CASCADE` is only a hard-delete backstop; merges
re-home *before* the delete (see Merge re-homing).

### Temporal integrity & cascades

**Invariant** (active edge): `[valid_from, valid_until] ⊆ [start, end]` of **both**
endpoint assignments, treating NULLs as open bounds. Cross-row → enforced by
**trigger + app guard, never a column CHECK** (Postgres CHECKs can't reference
other rows). Same shape as the #307 org-lifespan invariant.

Enforcement split:

- **Admin / direct writes** → `check_edge_within_assignments()` app guard raises
  `EdgeOutsideAssignmentWindow` (409). App-layer only, **no blanket DB invariant
  trigger** — a hard `BEFORE` trigger cannot distinguish admin writes from the
  observation path and would block the latter's "record freely" contract (the
  same reason #307 is enforced app-layer only). Cascade + audit are the DB-side
  integrity layer.
- **Observation path** → records freely; a daily audit
  (`scripts/audit_assignment_relationship_windows.py`) reconciles, `--execute`
  clamps. (Mirrors `audit_org_lifecycle_assignments.py` under #307.)
- **Endpoint mutation cascade** → trigger `trg_cascade_assignment_relationships`
  on `role_assignments AFTER UPDATE`, firing when `start_date` / `end_date` /
  `archived_at` change:

  | Endpoint change | Cascade on dependent edges |
  |---|---|
  | `archived_at` set | set edge `archived_at` (archive) |
  | end moved earlier / start moved later | **clamp** edge `valid_until` / `valid_from` to the new intersection |
  | clamp inverts window (`valid_from > valid_until`) | archive the edge |

Auto-clamp is intended and **silent-but-emitted**: every cascade is an `UPDATE`
on the edge → fires the edge's own change trigger, so clamps/archives surface in
the change feed rather than mutating state invisibly. Hard-delete of an endpoint
→ FK `ON DELETE CASCADE` (rare; archived-first policy).

### Observation path + change feed

**Own entity_type** `role_assignment_relationship` added to the `entity_type`
CHECK on `entity_changes`, `deleted_entities`, and
`api_key_entity_subscriptions`; added to the `fn_record_entity_change` CASE with
a new `trg_entity_changes_role_assignment_relationships` trigger. Each schema
change ships as an idempotent reconciliation `DO` block (the #312 inline-drift
rule) so it reaches existing DBs, not just fresh ones.

Plus a **touch trigger** `trg_touch_assignments_on_relationship_change` bumping
**both** endpoint assignments' `updated_at` (copy of
`trg_touch_jurisdiction_on_relationship_change`). Net: the edge surfaces (a) as
its own addressable/retractable feed entity **and** (b) to existing per-assignment
subscribers via the touch. Public `/changes` and `/subscriptions` entity_type
enums extended accordingly.

**Observation transport** — pm-native only (observers reference assignments by
`pm_assignment_id`, already observable via #311; a natural-key-of-both-assignments
path is far heavier and out of scope):

- `POST /api/v1/assignment-relationships/observations` — **partial-success** list
  (per-item savepoint + disposition + reason slug), mirroring the event-native
  path (#321). Body per item:
  `{from_pm_assignment_id, to_pm_assignment_id, rel_type, valid_from?, valid_until?, notes?, pm_relationship_id?, op?}`.
- Identity `(from, to, rel_type)` → **refine-in-place**; `pm_relationship_id` →
  direct refine; `op="retract"` → archive + anti-resurrection (the #322 event
  model).
- `source_key_id` **same-or-NULL** provenance gate on updates.
- Read: `GET /api/v1/assignments/{pm_assignment_id}/relationships`.
- Scopes: `assignment_relationships:read` / `assignment_relationships:write`
  (specific, not a broad `relationships:*`).

### Merge re-homing (in scope)

`rehome_assignment_relationships(db, losing_assignment_id, surviving_assignment_id)`
added to `src/core/ancillary_migrate.py` — re-points `from_assignment_id` /
`to_assignment_id` onto the survivor, dedups on identity collision (keep one,
archive the other). Wired **before the assignment DELETE** in all three merge
paths that collapse assignments: `people_merge.py`, `orgs_roles.py::role_merge`,
`orgs_merge.py` role-pair. The re-home `UPDATE` self-emits via the touch/change
trigger (#327 model — no manual `entity_changes` emit). Orphan audit
(`scripts/audit_ancillary_orphans.py`) gains an `assignment_relationship` scope.

### Admin surface

Mirror `admin/jurisdictions_relationships.py` + the jurisdictions detail panel.
New `src/api/admin/role_assignments_relationships.py`: a "Relationships" panel on
the role-assignment detail rendering **both directions** ("serves →" and
"← served by"). HTMX partial CRUD with `flash_trigger` + `with_flash` non-HTMX
fallback (STYLE.md §32 / #351). Typeahead to pick the other assignment.

### Backfill (in scope)

`scripts/backfill_assignment_relationships.py`: for the 3 concrete rows —

| Role ID | Staffer | Principal (in title) |
|---|---|---|
| `01KV6PR793RH66X1AF3TR8DV70` | Kate Armstrong | Sen. June Robinson |
| `01KV6PR6SSMT8FGNJFCGFRMA9P` | Joren Clowers | Rep. Shelley Kloba |
| `01KV6PR3S4T3GGQSEJ4W40NB15` | Coco Chang | Sen. Saldaña |

parse the principal name from the role title → resolve the principal person →
their `state_senator` / `state_representative` seat assignment overlapping the
staffer's window → mint an edge with `valid_*` = window intersection.
**Heuristic, supervised** (dry-run → `--execute`). `notes` are left untouched;
the operator cleans up from the emitted match list once this lands.

## Key decisions & rationale

- **RA→RA, not person→person** — preserves the object assignment context on both
  sides; matches how turnover actually works (new term = new assignments = new
  edge). (User directive.)
- **Principal side references the assignment, not the person** — the relationship
  only means something in the capacity of a specific term; cross-term continuity
  is a new edge to the new seat assignment, not a mutated one.
- **Own change-feed entity_type** (broader than the `jurisdiction_relationships`
  precedent, which is admin-only + touch-only) — so the edge is independently
  addressable for retract/archive. Touch-both-endpoints is the secondary signal.
  (User directive.)
- **Auto-clamp-and-emit** on endpoint shrink, not reject — keeps "no orphaned
  invalid records" true without operator babysitting; emission keeps it
  observable. (User directive.)
- **Enforcement split** (admin enforces / observation records + audit
  reconciles) — reuses the #307 pattern; observers won't supply perfectly-aligned
  windows.

## Out of scope

- Non-pm natural-key observation of the edge (identity is two `pm_assignment_id`s).
- Generalizing the UI/vocabulary beyond legislative staff — the type catalog
  permits future slugs (chief_of_staff, district_staff, …) but no new surfaces
  are built now.
- The operator's manual `notes` cleanup of the 3 backfilled rows.

## Testing (TDD, red → green → refactor)

- Cascade-trigger unit tests: archive, clamp-earlier-end, clamp-later-start,
  clamp-inverts→archive.
- Invariant guard: app `check_edge_within_assignments` + DB trigger backstop.
- Observation: refine-in-place, `pm_relationship_id` refine, `op="retract"` +
  anti-resurrection, `source_key_id` same-or-NULL gate, partial-success
  dispositions.
- Merge re-home: both-side re-point, identity-collision dedup, across all three
  merge paths.
- Audit reconcile (dry-run + `--execute`), orphan-audit new scope.
- Backfill dry-run.
- Endpoint integration tests (rollback client per CONVENTIONS.md).
