# #882 — a stable `reason_code`, one provenance name, readable ownership

Issue: #882 (asked on CannObserv/cli#1386). Operator decisions 2026-11-25, comment 7172492503.

## Goal

A producer can tell, before writing, whether an id-addressed write to a record will hold. After a refusal it can act on why, without matching the free-text `reason`.

## Approach: two PRs

### PR 1: `reason_code` (minor bump)

- **`ObservationReasonCode` (`StrEnum`)** is one enum covering every refusal `ObservationResponse` can carry. `ObservationResponse.reason_code: ObservationReasonCode | None`: set exactly when `disposition == "rejected"`, `null` otherwise. OpenAPI carries the enum, so the generated client has it.
- **`ObservationRejected(code, detail=None)`.** The code is required. `detail` keeps today's text, so `reason` is unchanged on the wire. The route handlers' `except` blocks build both fields from the exception. The `IdentifierConflict`, `UniqueViolationError` and constraint branches set their codes directly. The resolvers' `(…, REJECTED, reason)` tuples carry a code too.
- **The code is the reason's slug,** the text before `": "`, with three exceptions:
  - `source_key_mismatch` (assignments, org parent) and `name_provenance_conflict` (names) both become **`provenance_conflict`**;
  - free-text validation messages (unknown link, contact or affiliation type; address normalisation; parent-lookup counts; internal identifier type) become **`invalid`**;
  - `"rejected"`, the resolver's `reason or "rejected"` fallback, becomes `invalid`.
- **Embedded events, citations and names** re-raise with their own enum's slug, so their codes are a subset of the new enum. A guard holds `EventRejectReason`, `CitationRejectReason` and `NameRejectReason` (as mapped) inside `ObservationReasonCode`.
- **`reason` text is unchanged this release,** `source_key_mismatch` included, as the decision requires. A follow-up issue switches the text to `provenance_conflict` one release later.
- **Sweep guard:** an AST test finds every `ObservationRejected(...)` and every resolver `Disposition.REJECTED` return under `src/`, and fails on one that passes no enum member. It also fails on any `ObservationResponse(disposition="rejected", …)` that leaves out `reason_code`.
- **Docs:**
  - PUBLIC_API.md: `reason_code` is the contract and `reason` stays diagnostic, with the code table: each code, its meaning, and whether it is transient.
  - API_ASSIGNMENTS.md: its 9 `source_key_mismatch` mentions become `provenance_conflict`, with a deprecation note on the text.
  - OBSERVATIONS.md: one name for the gate.

### PR 2: ownership (minor bump)

- **`AssignmentDetail.ownership`** and **`OrgDetail.parent_ownership`**, each `"self" | "other" | "none" | "dataset"`. The org field is named for the only column its gate guards, `parent_id`. The org's `source_key_id` is claimed on the first parent write, not at creation.
- **Precedence: `dataset` first.**
  - `dataset`: the subscribed dataset owns the row, and its nightly apply reverts any observation write.
  - Otherwise, from `source_key_id`: the calling key's own is `self`, another key's is `other`, and NULL is `none`.
- **What `dataset` tests:** the same test the curation overlay's `in_scope` uses, a `producer_crosswalk` anchor in `IN_SCOPE`:
  - kind `assignment`, for the assignment's dates;
  - kind `organization`, for the org's parent.
  - This over-approximates for an anchored org whose dataset emits no parent row (`retraction: none`: silence). The overlay draws its scope the same way, so the two agree.
- **Surfaces:** `getAssignment`, `getOrg`, `batchGetOrgs` (its ETags must equal `getOrg`'s). List rows carry no ownership. A list ETag is a collection watermark, and a per-row key-relative field would break it.
- **ETag:** `make_etag(id, updated_at, *extra)` appends the key-relative value. Two keys reading one row then get different validators, and a dataset adopting a row changes it without touching `updated_at`. `conditional_response` is unchanged.
- **The principle and its guard.** OBSERVATIONS.md: "a `source_key_id` gate exists exactly where the observation API can overwrite or remove a value; who created a row is not who may overwrite it." The guard asserts that the set of tables holding `source_key_id` (read from `schema.sql`) equals a declared list, each entry naming its gate. A new column, or a gate on people or roles, fails it until it is declared and documented.

## Key decisions

| Decision | Why |
|---|---|
| Code = the slug, not a coarse class | Today's slugs already carry the meaning (`person_not_found`, `start_date_conflict`); a class like `not_found` would throw it away. One rule ("the slug before `: `") is easy to state and to guard. |
| `reason` text untouched in PR 1 | Required by the decision; the code is the contract, and the text renames a release later. |
| `dataset` beats `self` | The gate ignores dataset ownership (#499 applier). A key could hold `source_key_id` on a row the dataset reverts nightly, so `self` there would be a false promise. |
| No refusal for writes to dataset-owned rows | Not in the decided scope. A follow-up issue: a write the nightly apply will revert should be refused as `owned_by_dataset`, as events already are (#592). |

## Out of scope

- Ownership on people or roles: no gate exists there (decision 4).
- Typing the per-item `reason` on the partial-success event, citation and relationship surfaces as enums. They are stable slugs already.
- Claims (#881).
