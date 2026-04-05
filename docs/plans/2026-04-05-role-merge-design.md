# Role Merge on Org Detail Page

## Goal

Enable manual merging of two roles within the same organization, directly from the roles table on the org detail page. Reuses the "Keep A / Keep B" pattern from people/org duplicate merge but triggered by manual checkbox selection rather than automated detection.

## Approved Approach

### UI: Checkbox selection mode with sticky merge bar

**Entering merge mode:**
- "Merge" button next to "+ Add role" (`btn--sm btn--secondary`). Hidden when org is archived.
- Toggles `data-merge-mode` on the table via JS (no server round-trip).
- Checkbox column appears on each row (hidden via CSS until merge mode active).
- Button text changes to "Cancel merge" while active.

**Selecting rows:**
- User checks exactly two rows. JS enforces cap — checking a third unchecks the oldest.
- After two checked, a sticky merge action bar appears at the bottom of `.table-wrapper` (`position: sticky; bottom: 0`).
- Bar text: "Keep **Role A title** or **Role B title**?" with two `btn--primary btn--sm` buttons.
- Each button is a form: `hx-post="/admin/orgs/{org_id}/roles/{winner_id}/merge/{loser_id}/"`, `hx-target="#roles-table tbody"`, `hx-swap="innerHTML"`, `hx-confirm="Merge {loser} into {winner}? This cannot be undone."`

**After merge:**
- Server returns refreshed `_role_rows.html` tbody with flash trigger.
- Merge mode auto-exits (hide checkboxes, hide action bar, reset button text).

**Client-side JS:**
- `role-merge.js` in `src/static/admin/`, loaded via `{% block extra_head %}` on org detail template.
- Handles: merge mode toggle, checkbox enforcement, action bar show/hide, URL construction.

### Merge Logic (server-side)

Endpoint: `POST /admin/orgs/{org_id}/roles/{winner_id}/merge/{loser_id}/`

Within a single transaction:
1. **role_assignments**: Delete conflicting assignments (same `person_id` + `start_date` as an existing winner assignment), then reassign remaining from loser to winner.
2. **import_provenance / field_confidence**: Reassign rows where `entity_type='role'` and `entity_id=loser` to winner.
3. **notes**: If loser has notes, append to winner's notes with merge context prefix (same pattern as people merge).
4. **Hard delete**: Remove loser role.

**Guards:**
- 404 if winner or loser not found.
- 409 if either role is archived.
- 409 if roles belong to different orgs (or don't match the URL `org_id`).

**Response:**
- HTMX: refreshed `_role_rows.html` tbody + flash trigger (success).
- Non-HTMX: redirect to org detail page.

### No schema changes

Manual-only merge requires no `duplicate_dismissals` table or detection queries.

## Testing Strategy

**Integration tests** (real DB):
- Merge reassigns all role_assignments from loser to winner
- Merge deletes conflicting assignments (same person + start_date) before reassign
- Merge reassigns import_provenance and field_confidence rows
- Merge appends loser's notes to winner's notes
- Merge hard-deletes the loser role
- 404 when winner or loser doesn't exist
- 409 when either role is archived
- 409 when roles belong to different orgs
- HTMX response returns refreshed tbody with flash header
- Non-HTMX response redirects to org detail

**Unit tests** (no DB):
- `role-merge.js`: checkbox cap enforcement, action bar visibility toggle, merge mode enter/exit, correct URL construction

## Out of Scope

- Automated duplicate detection for roles (future enhancement)
- Merge from role detail page
- Cross-org role merge
