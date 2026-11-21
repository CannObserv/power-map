# Key-written names: provenance on the names panel and a review list (#847)

## Goal

From #845 on, an API key can add `variant` names to people, and they are public
at once. The safeguard is review after the fact. A curator must be able to:

1. see, on a person's names panel, which key wrote each name;
2. list every person name one key wrote, newest first, without querying the DB.

## Where the per-key list lives

**Recommendation: a new read-only page under Activity, `/admin/activity/names/`
("Key-written names"), next to Activity › API requests.** The issue offered two
options. Neither fits as well:

| Option | Why not |
|---|---|
| Section on an API key's page (Settings › API keys) | Every route there is owner-scoped (`WHERE … AND user_id = $2`, `settings_api_keys.py`). A curator sees only keys they created, so Observo's names would be reviewable only by whoever made Observo's key. Making that page cross-owner changes what it is. It also has no full per-key page today, only an HTMX scope panel. |
| Filter on the people list | The list is one row per person, sorted by name. Its query (`query_people_rows`) is shared with the merge flow in `people_merge.py`. The ask is one row per name, newest first. A filter would need a names column, a second sort order, and changes to a query the merge path depends on. |

Activity is already where admin shows what keys did, across owners:
`activity_requests.py` uses `get_admin_user`, joins `api_keys` for the label,
and filters by `?key=`. The new page follows that pattern.

## Approach

### Names panel (person and org, through the shared factory)

- `_fetch_names_for_rows` in `_names_shared.py`, both branches: add
  `LEFT JOIN api_keys k ON k.id = <alias>.source_key_id`, selecting
  `k.label AS source_key_label`. The org panel gets this without extra work.
- Read row: when the stamp is set, show a small "via ‹label›" badge. On a
  person row it links to `/admin/activity/names/?key=<id>`. A NULL stamp (any
  admin, importer or applier write, or a deleted key) shows nothing. Jinja
  autoescapes the label. No flash includes it, because this work adds no
  mutation.
- The citation count is already on each row, so the key and the citations
  appear side by side.

### Review page: `GET /admin/activity/names/`

- Query params: `key` (optional, default all keys) and `page`. The key
  dropdown lists keys that hold at least one stamped person name, from any
  owner, by label.
- One row per `person_names` row with `source_key_id IS NOT NULL`. Columns:
  Added (`created_at`), Name, Type, Person (from `v_person_display_names`,
  linked to `/admin/people/<id>/`, with an Archived badge), Key, Citations
  (count).
- `ORDER BY pn.created_at DESC, pn.id DESC`, which ends on a unique column
  (#297). Page size 50, using `pagination_context`.
- **No status axis (#306).** Names are not archived. A filter on the person's
  status would be exactly the #306 trap of hiding matches by default. The
  person's archived state shows as a badge, so nothing is hidden.
- **Non-public names (needs your approval).** Keys can land non-public names:
  `trg_deadname_visibility` turns a key-sent `deadname` into `legal_only`, and
  a curator can later set any key-written name to `hidden`. Recommendation: keep
  the row, but withhold both the text and the type. The SQL returns
  `CASE WHEN visibility = 'public' THEN … END`, so the text never leaves the
  database. The row reads "Not public — review on the person page" and links to
  the person, where the existing disclosure toggle shows the name.
  - This is a narrow exception to the `docs/NAMES.md` visibility rule. The row
    shows that a non-public name exists and which person and key it belongs
    to. It never shows the name's text or type.
  - Why: a key writing a deadname is the main case this review exists for.
    Dropping the row would hide it.
  - Alternative: drop such rows and show only "N non-public names not listed".
  - The new query goes on the `ALLOWED_DIRECT_ACCESS` list with a
    `# visibility-allowlist (issue #121)` reason, and NAMES.md records the
    exception.
- Navigation: add an Activity sub-item next to API requests.
- The page is read-only, so the mutation-fallback sweep does not apply.

### What the list can and cannot show

- It shows names held now. A retract deletes the row, and a deleted key's
  stamps become NULL (`ON DELETE SET NULL`).
- The stamp records the first writer only. A re-send that matches an existing
  row does not change it.
- After a key rotation, only a curator can remove the old key's names, because
  a retract must come from the key that wrote the name. This page is how they
  find them.

## Scope decisions

- **Person names only on the list.** Org names get the badge on the panel
  only. All 1,781 org names are unstamped and Observo writes none.
- **Other stamped tables are out of scope.** `role_assignments` (`schema.sql`
  :4276), `organizations` (:4293, ownership of `parent_id`),
  `role_assignment_relationships` (:606, FK at :3189), `citations` (:885) and
  `entity_events` (:2728) all carry `source_key_id`. The page could later gain a
  "kind" selector for them.
- **Identifiers are out of scope.** `identifiers` has no `source_key_id`.
  Covering them would take:
  - a column with a reconciliation block;
  - stamping in the identifier observation writer and in the #848 replace path;
  - accepting that every existing row stays NULL.
- **No schema change and no index.** No production row is stamped today, and
  `person_names` is about 5k rows. A partial index on `source_key_id` can wait
  until the list is slow.
- **No public API change.** No version bump, and the served OpenAPI spec is
  unchanged. Both are checked before the PR.

## Out of scope

- Bulk actions on the review page, such as retracting or hiding names. The
  curator acts on the person page.
- Stamping admin, importer or applier writes.
