# power-map — The producer contract: refusal codes, record ownership, minted ids

What a producer acts on around an observation write (#882): the stable `reason_code` on a refusal, and a record's `ownership` before a write; and how it creates a person or organization that holds no external identifier (#879). The write semantics themselves are in [OBSERVATIONS.md](OBSERVATIONS.md); the response shape is in [PUBLIC_API.md](PUBLIC_API.md) § Observation writes.

---

## Reason codes

A code is the slug at the head of `reason` (the text before `: `), except for the three cases in the last two rows.

| Area | Codes |
|---|---|
| Resolving the entity | `unknown_identifier_type`, `pm_id_not_found`, `person_archived`, `organization_archived`, `role_archived`, `jurisdiction_archived`, `role_assignment_archived`, `entity_type_not_creatable`, `entity_type_mismatch`, `jurisdiction_new_requires_create_data`, `unknown_jurisdiction_type`, `unique_violation` |
| Roles | `org_not_found`, `role_type_not_found`, `jurisdiction_not_found`, `role_type_required_for_jurisdiction`, `qualifier_required`, `qualifier_forbidden`, `title_required` |
| Assignments | `person_not_found`, `role_not_found`, `assignment_not_found`, `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` |
| Org parent | `parent_not_found`, `parent_cycle`, `active_on_archived_org` (`org_not_found` as for roles) |
| Assignments and org parent | `owned_by_dataset`: a subscribed dataset owns the row, and its nightly apply would revert the write; correct it at the dataset. Read `ownership` / `parent_ownership` first (#882) |
| Identifiers | `identifier_conflict`, `identifier_taken`, `identifier_type_mismatch`, `identifier_locked`, `replaces_unsupported`, `replaces_not_held` |
| Embedded events, citations, names | the per-item slugs below (`linked_entity_unresolved` and `entity_unresolved` are transient), and `name_is_canonical`, `name_is_last`, `name_has_readings`, `name_has_foreign_citations` |
| Any write | `identity_immutable`, `missing_required_field`, `db_constraint_violation`, `invalid` |
| **`provenance_conflict`** | The same-or-NULL `source_key_id` gate, on every surface: another key owns the row. Its `reason` text still reads `source_key_mismatch` (assignments, org parent) or `name_provenance_conflict` (names) until #888 |
| **`invalid`** | Also every free-text validation message: an unknown link, contact or affiliation type, an address the normaliser refused, a parent lookup that matched other than once. An additional identifier of an unknown type is `unknown_identifier_type` |

**One gate, one name (#882).** Wherever the same-or-NULL `source_key_id` gate refuses (assignments, org parent, events, citations, RA→RA edges, names), the refusal's `reason_code` is `provenance_conflict`. Every refusal carries a code, the contract a producer acts on; the table above lists them, and `tests/core/test_refusal_code_sweep.py` holds every refusal site under `src/` to one.

## Record ownership

**The principle.** A `source_key_id` gate exists exactly where the observation API can overwrite or remove a value. Who created a row is not who may overwrite it: the column never doubles as creation provenance. `tests/core/test_source_key_gates.py` holds every table carrying the column to a declaration:

| Table | The column is |
|---|---|
| `role_assignments` | a gate: the id-addressed tenure update and retract (#311/#391) |
| `organizations` | a gate on the **parent** only: an id-addressed reparent (#334). Claimed on the first parent write, not at creation |
| `entity_events`, `citations`, `role_assignment_relationships` | a gate: the id-addressed refine and retract (#321/#319/#301) |
| `person_names` | a gate: the name retract (#845) |
| `organization_names` | a stamp: the API only adds org names (#162) |

People and roles carry none: nothing on either row is overwritable through the API.

**A dataset owns some rows outright.** A row whose `producer_crosswalk` anchor is in scope (`live`, `merged`) is the subscribed dataset's, and its nightly apply writes it without consulting `source_key_id` (#499). An observation write there would be reverted, so:

- an id-addressed assignment update or retract, or an id-addressed reparent, is refused **`owned_by_dataset`**, checked before the `source_key_id` gate, whoever holds the stamp;
- an identical assertion never claims a dataset row (#478's claim stops there);
- the natural-key close-enrichment and the write-if-null parent fill are **skipped**, not refused, so the rest of the observation lands, and the field is listed in `unapplied`.

**Ownership on reads.** `AssignmentDetail.ownership` and `OrgDetail.parent_ownership` answer `self | other | none | dataset` relative to the calling key, from one helper the refusal shares (`src/core/record_ownership.py`). `dataset` comes first; otherwise the gate's own answer (`none` = unowned, claimable). The detail ETag carries the value. List rows carry none.

**`Ownership` is a closed set.** `self`, `other`, `none` and `dataset` are the whole vocabulary: a fifth value would be a breaking change, so it would ship as one. (Reason codes are open — see PUBLIC_API.md: a minor release may add one.)

---

## Creating a record with no external identifier

A person or organization has no natural key (a name isn't identity), so a producer that knows only a name creates one by minting a ULID and observing *by* `person_co_operator` / `org_co_operator` (#879, CannObserv/cli#1386). A role and an assignment need no such id: each re-sends safely on its natural key ([API_ROLES.md](API_ROLES.md), [API_ASSIGNMENTS.md](API_ASSIGNMENTS.md)).

- **Create, re-send, recover.** An unknown value creates the record (`new`); a re-send of the same value attaches to it (`auto-attached`), with no retry window to outlive. After a timeout, `people/search` / `orgs/search` with `identifier_type` + `identifier_value` say whether the create landed. Address the record by its `pm_*` id from then on.
- **Mint once per real person or org, and keep the value.** A fresh ULID for someone already in Power Map is a duplicate: finding them first (name search, operator confirmation) is the producer's job. Citations are recommended, not enforced.
- **One value, one record.** The value is under `uq_identifiers_cannabis_observer` ([OBSERVATIONS.md](OBSERVATIONS.md) § Identifier types): attaching it to a second record is refused `identifier_taken`. A curator merge moves it to the survivor; `replaces` swaps it ([ANCILLARY.md](ANCILLARY.md) § Replacing).
- **Provenance.** People carry no `source_key_id`, so the slug is also the record of which producer minted the record.
