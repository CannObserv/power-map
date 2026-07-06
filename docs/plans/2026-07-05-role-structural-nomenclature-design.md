# Role structural-field nomenclature — retire "seat" for Role Type / Jurisdiction / Qualifier

**Date:** 2026-07-05
**Status:** Approved (brainstorming session)
**Follow-on to:** #261 (seat-Role model), #264 (admin surfacing), #267 (title synthesis), #268 (role-types catalog)

## Goal

Make the three canonical fields — **Role Type**, **Jurisdiction**, **Qualifier** — the vocabulary end-to-end (DB → public API → admin UI → docs). Retire "seat" (a *composite* noun for "a role that carries all three") as user- and dev-facing vocabulary. Where a composite phrase is unavoidable, use **"a role with a jurisdiction" / "jurisdictional role"** — not "seat", not "districted".

Prompted by the #264 CR: the admin UI had been renamed "Seat" → "Qualifier" (CR-1 fix 4), which conflicted with the field name and left the admin at odds with the rest of the system. The audit showed "seat" is otherwise consistent across DB/API/docs — so rather than a half-rename, align the *whole stack* to the three field names.

## Approved scope — Tier A + B + C

- **A — admin** UI labels, error strings, internal handles, comments.
- **B — docs** prose (AGENTS.md, CONVENTIONS.md, PUBLIC_API.md, STYLE.md).
- **C — public API + DB** (breaking): the `is_seat` field, the `seat_title_unavailable` reason code, the DB column/index/constraint names.

## Naming decisions

| Layer | Old | New | Breaking |
|---|---|---|---|
| Public API field (`RoleTypeItem`) | `is_seat` | `expects_jurisdiction` | **yes** |
| Public reason code (rejected observation) | `seat_title_unavailable` | `role_title_unavailable` | **yes** |
| DB column | `role_types.is_seat` | `role_types.expects_jurisdiction` | migration |
| DB unique index | `uq_role_seat` | `uq_role_structural` | migration |
| DB check constraint | `chk_role_districted_needs_type` | `chk_role_jurisdiction_needs_role_type` | migration |
| Core module | `src/core/seat_title.py` | `src/core/role_title.py` | internal |
| Core functions | `synthesize_seat_title`, `wa_legislative_seat_title` | `synthesize_role_title`, `wa_legislative_role_title` | internal |
| Admin route | `POST/GET /roles/{id}/inline/seat/` | `…/inline/structural/` | internal |
| Admin element ids | `seat-field`, `seat-details` | `structural-field`, `structural-details` | internal |
| Admin CSS class | `badge--seat` | `badge--role-type` | internal |
| Admin partials | `_seat_read.html`, `_seat_form.html` | `_structural_read.html`, `_structural_form.html` | internal |
| Admin ctx helper | `_seat_form_ctx` | `_structural_form_ctx` | internal |
| Admin UI field labels | "Office" / "Seat"·"Qualifier" umbrella | **Role Type / Jurisdiction / Qualifier**; badge shows the **role-type display name** | no |
| Composite prose | "seat", "districted seat", "districted role" | "a role with a jurisdiction" / "jurisdictional role" | no |

### Badge = role type (resolves CR findings 4/11)

The list + detail badge stop saying "Seat"/"Qualifier" and instead show the **role-type display name** (e.g. "State Representative"), keyed on `role_type_id`. This is canonical, informative, absent on plain roles, and never asserts the "seat" composite — dissolving the over-labeling of a role that has a role type but no jurisdiction. `role_type_name` is already selected by both the list and detail queries.

## Migration

Idempotent `schema.sql` DO-blocks, applied by `apply-schema.sh`:
- `ALTER TABLE role_types RENAME COLUMN is_seat TO expects_jurisdiction` (guarded on the old column existing); fresh DBs create `expects_jurisdiction` directly.
- Rename index `uq_role_seat` → `uq_role_structural` and constraint `chk_role_districted_needs_type` → `chk_role_jurisdiction_needs_role_type` via guarded drop + recreate.
- Update the `role_types` seed `INSERT … ON CONFLICT` to the new column.
- `chk_role_qualifier_needs_jurisdiction` already field-named — unchanged.

## Versioning & rollout

- **0.6.0 → 0.7.0** (breaking public field + reason code). `pyproject.toml` + `package.json` together.
- **Consumer break:** the sibling **usa-wa** repo mirrors `is_seat` (#268 / usa-wa#68). Renaming to `expects_jurisdiction` breaks that mirror. Tracked as a **follow-up issue in usa-wa** — not blocking this PR, but the two must land close together.

## Testing (TDD)

Red-first per layer: rename assertions in `test_role_types.py` (public field), `test_resolve_role_seats.py` (reason code), `test_schema_role_seats.py` (index/constraint), `test_seat_title.py`→`test_role_title.py`, and the admin `test_roles.py` (labels, badge, route, error strings). Full admin + core + public suites must stay green after the rename; `apply-schema.sh` must run clean against the live dev DB.

## Relationship to the #264 CR branch

This lands on the same branch as the #264 CR fixes (1–3, 5–7, 9, 12), which stand. It **supersedes** CR-1 fix 4 (the "Qualifier" labels) — those are replaced by the three-field scheme.

## Out of scope

- Historical issue titles (#261/#264/#267/#268) and git history keep "seat" — not rewritten.
- No behavioral change: matching, synthesis, constraints, and the split-uniqueness model are identical; this is a pure nomenclature + one advisory-field rename.
