# Member-elect roles from usa-wa#600 — design (#883)

## Goal

Get the usa-wa nightly building again and carry usa-wa#600's member-elect
assignments into power-map, so the pending desired-state diff can reach its
supervised `--execute`.

## What usa-wa publishes (snapshot `v20261127T080315Z`)

- 96 `seat-elect:` roles: 79 `state_representative_elect` (district + `Position N`),
  17 `state_senator_elect` (district, no qualifier), in the House / Senate orgs.
- One capacity period each: from 2026-11-03, `valid_to` open, `max_holders` 1.
  The role outlives its holder.
- 96 `principal` spans (`chamber-house-elect` / `chamber-senate-elect`),
  2026-11-03 → 2027-01-10, `is_active` false → PM `is_current` false, ended.
- A brand-new role publishes with a null `entity_id` on its first night and is
  bound the next; WSL keeps listing winners until certification (~12-03).

## Failure modes found

1. **Build (11-27):** `not_null_stg_usa_wa__roles_entity_id` errored on the 96
   unbound roles; 34 nodes skipped, no dry run. Recurs each night WSL lists a
   new winner.
2. **Apply (once bound):** `role_types` lacks both slugs. `desired_roles` is an
   entity binding, so `_resolve_identity` marks each create `stale` ("names
   role_types …, which PM does not carry") and everything under it stale too.
   `stale 0` → exit 3, and no `--execute` of the rest of the diff. (Not the
   `_diff_child` `ApplierError`: no child binding looks up `role_types`.)

## Approved approach: (a) + guard (warn) + (c), one PR

**(a) Seed the two types** in `_seed_role_types` (#458 stage), beside their seats:

| slug | expects_jurisdiction | requires_qualifier | forbids_qualifier | occupancy |
|---|---|---|---|---|
| `state_representative_elect` | TRUE | TRUE | FALSE | `single` |
| `state_senator_elect` | TRUE | FALSE | FALSE | `single` |

- Jurisdictional, so out of the prefix rule's scope (`test_role_type_prefix_sweep.py`).
- `uq_role_structural` keys on `role_type_id`: no collision with the seat roles.
- Seat-occupancy audit: one member-elect per seat-elect role (`max_holders` 1);
  a re-elected incumbent holds two *different* roles, which is not a pair.
- `role_types` has no remote write path (#302): **deploy before the execute
  that applies them.** Minor bump, annotated tag.

**Guard: `published_role_type_unknown_to_pm`, severity warn.** One row per
published `role_type` absent from `stg_pm__role_types`: slug, role count, a
sample `role_key`. Warn, not error: the applier already halts on such a create
(`stale`, exit 3, every row named); an error at build only repeats failure
mode 1 — one test skipping every model and hiding the rest of the diff. It also
names an anchored role whose type moved, which the applier ignores silently
(identity is written once).

**(c) Tolerate the one-night registration lag.**
- `not_null` on `stg_usa_wa__roles.entity_id` → warn up to 150, error above (CR 1):
  a seat-elect cycle is at most 123; more is a registrar regression, which halts
  the build instead of reaching the dry run as an archive per anchored role.
- `desired_roles` skips a role with no `entity_id` (it can never be anchored).
- `desired_role_assignments` skips an **unanchored** span whose role is
  published but unbound; an anchored span is kept (absence archives).
- `role_create_pm_would_refuse` stops naming unbound roles (they are waiting,
  not refused); `unresolved_role_capacity_periods` keeps naming their periods.

## Out of scope

- Mapping `span_kind`: the mapping reads none.
- Asking usa-wa to bind ids the night they publish: raised on #883, not required.
- The supervised `--execute` itself: the operator's, prepared separately.
