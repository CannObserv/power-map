# The roles model: design

Part of #500 (delivery 2, PR B) under epic #490. Approved 2026-09-16. PR A
(#527, v0.51.0) built `retraction: archive`, identity-carrying creates and the
overlay map on assignments; roles reuse that machinery, and need four things it
does not have.

## Goal

Apply usa-wa's published `roles` dataset to PM's `roles`:
- create a role PM lacks, with the structure PM's guards require;
- keep its title, which the producer owns and a curator can pin;
- archive a role the snapshot no longer carries, unless PM rows still depend on
  it;
- restore one the applier archived when it comes back.

## Measurements (production, 2026-09-15, read-only)

- **312 anchored roles, all `live`.** Title, role type, qualifier and
  organization match the dataset on every one, so the first diff is 312 no-ops
  and nothing else.
- **147 are districted** (a jurisdiction, hence the structural identity index)
  and **165 are not** (title identity).
- **Every dataset role is anchored:** no creates today.
- **PM holds 2,169 live roles.** The other 1,857 are outside the producer's row
  scope and are never read here.
- **Districts are one to one:** 49 `legislative_district` jurisdictions, slugs
  `usa-wa-ld-<n>`, no superseded rows — so `district` resolves by slug.
- **Role types line up:** `committee_member` (157), `state_representative`
  (98), `state_senator` (49) and `party_member` (8) are all `role_types` slugs.

## Decisions

| Question | Decision | Why |
|---|---|---|
| What the producer owns | Identity at create, then the title | #490 already gives the producer the title and PM the overlay. Structure is identity: when it changes, usa-wa mints a new `role_key`, which arrives as an archive plus a create |
| Role creates | Generic, checked before they are planned | `roles` enforces the qualifier rules with triggers (#273, #302), so a create that breaks one must never be planned. A lookup that misses (#302's new role type) is `stale`, not a failed write |
| A role archive with live assignments | `conflict` unless the same run archives them | The ordinary case is a re-key: usa-wa drops the role's spans with it. A curated or another producer's assignment left on an archived role is a person's call |
| Where the rules live | The manifest, extended again | The applier stays generic and the ontology stays in the mapping project (#497). The alternative — a role hook in core — makes a second copy of PM's role rules |

## Models

- **`stg_usa_wa__roles`** gains the columns PR A left unread: `role_type`,
  `name`, `org_entity_id`, `district`, `qualifier`, `span_kind`.
- **`desired_roles`** — entity binding, `retraction: archive`. One row per
  published role, keyed by `entity_id`, `pm_id` from the crosswalk export,
  scoped as every model is (`resolution is null or in ('live','merged')`). It
  carries producer ids and slugs, never PM ids:
  `org_producer_id`, `role_type`, `jurisdiction_slug`
  (`'usa-wa-ld-' || district` for a chamber seat, else null), `qualifier`,
  `title`.
- **`desired_role_titles`** — column binding on `roles.title`, owned and
  pinnable (`role.title`). No `asserts_null`: a role's title is never absent.
- **The qualifier rule is a model rule.** `export_pm_tables` adds `role_types`
  (`slug`, `requires_qualifier`, `forbids_qualifier`). `desired_roles` drops a
  **create** whose qualifier its type requires or forbids, and
  `tests/role_create_qualifier_invalid.sql` (warn) names it. Only creates are
  ever dropped — dropping an anchored role would read as absence and archive a
  live role. Its assignments then read `stale` until someone fixes it, which is
  the safe direction.

## Manifest

```yaml
desired_roles:
  entity: role
  key: [producer_id]
  pm_key: pm_id
  retraction: archive
  owned_columns: []
  target:
    shape: entity
    table: roles
    identity:
      org_producer_id:   {column: organization_id, entity: organization}
      role_type:         {column: role_type_id, lookup: {table: role_types, from: slug, to: id}}
      jurisdiction_slug: {column: jurisdiction_id, lookup: {table: jurisdictions, from: slug, to: id}}
      qualifier:         qualifier
      title:             title
    unique_live:                                  # both partial identity indexes (#261)
      - columns: [organization_id, role_type_id, jurisdiction_id, qualifier]
        when: {jurisdiction_id: not_null}         # uq_role_structural
      - columns: [organization_id, title]
        fold: {title: lower}                      # uq_role_org_title
        when: {jurisdiction_id: null}
    dependents:                                   # what must not be left behind
      role_assignments: [role_id]
desired_role_titles:
  entity: role
  key: [producer_id]
  pm_key: pm_id
  retraction: none
  owned_columns: [title]
  overlay: title
  target:
    shape: column
    table: roles
    columns: {title: title}
```

A plain `unique_live` list of column names stays valid, so assignments are
untouched.

## Applier

- **Identity lookups.** `{column, lookup: {table, from, to}}`, resolved through
  the store with the query the child binding already uses. A value with no match
  makes the create `stale`, naming it — the #302 shape, where `role_types` has
  no remote write path and a new type waits for a PM deploy.
- **The index list.** Per row the engine picks the index whose `when` its
  identity meets; `slot_holders` takes that index and probes with its fold
  (`lower(col) = lower($n)`) and its own partial predicate, so a row under the
  other index never counts as a holder. Creates, restores and title moves check
  the chosen index, and rivals inside one run are counted per index.
- **Archives are decided first.** Every archiving binding's absences are
  computed before any binding's restores and creates, so the guard can see them.
  The `dependents` guard then turns a role archive into a `conflict` when live
  rows still name the role beyond those this plan archives, giving the count and
  up to five ids. The role keeps its slot, so a create or restore that counted on
  it conflicts too — never a collision.
- **Write order is unchanged:** archives, restores, creates (roles before the
  assignments naming them, which `_entity_order` already does), children,
  columns. A role archive cascades nothing in the database, so there is no
  preview to make.

## Admin

- A `role.title` slot: the registry, `role` in the overlay routes' entity and
  prefix maps, a slot line on the role page and a note on its title form.
- `tracked()` on `roles_detail.py`'s inline title edit and its structural edit
  (tracking `title` only — the structural fields are identity and PM may move
  them freely).
- The #498 sweep's regex gains `UPDATE roles SET … title =`, and
  `overlay_field_unmapped.sql` gains `role.title`, retiring its "role: #500's
  PR B" note.
- Org merges already re-home `role` pins (#514), and there is no standalone
  role merge, so nothing else moves.

## Expected first dry run

`desired_roles` 312 no-op, `desired_role_titles` 312 no-op, no creates and no
archives; the assignment diff unchanged. The digest changes because the tables
are new, so the streak restarts — the blocking counts do not move.

## Tests

- **Loader:** identity lookups, the index list (`when`, `fold`), `dependents`,
  and each refusal.
- **Diff:** a lookup miss is `stale`; each index chosen by its condition; the
  fold; the guard with and without the plan archiving the dependents; the
  knock-on conflict on a create that wanted the freed slot.
- **Postgres store:** folded and conditioned probes, and lookups.
- **Integration:** a role re-key (old archived, new created with its
  assignments) in one verified transaction; the guard's conflict before any
  write; the qualifier triggers never reached.
- **dbt fixtures:** the district slug, the qualifier warn, a pinned title.
- **Admin:** endpoint tests for the slot and the two edit sites, plus the sweep.

## Deploy

No schema change. `export_pm_tables` gains `role_types`, so an ordinary pull and
restart with no timing constraint. Version 0.52.0.

## Out of scope

- **#501's supervised execute**, which sizes the first archiving run.
- **The observation API**: usa-wa's role writes there retire with usa-wa#314.
- **Roles outside usa-wa's row scope** — 1,857 of PM's 2,169 live roles.
- **Supersession pairing for roles**: a re-key is an archive and a create, and
  the pairing has no reader.
