# Org Detail UI — Round 3 Design

**Date:** 2026-03-24
**Builds on:** `docs/plans/2026-03-23-org-detail-ui-round2-design.md` (#28)

---

## Goal

Apply the UX patterns established in #28 to the remaining sections of the Organization detail screen: restructure Hierarchy into an entity-card, consolidate Addresses + Contact Methods into a new "Contact Information" entity-card, apply form-group / pill-toggle / button-group consistency to all remaining form rows, and move `+ Add` buttons into subsection header rows for Links and Identifiers.

---

## Approved Changes

### 1. Section layout (detail.html)

| Before | After |
|---|---|
| Details (entity-card) | Details (entity-card) — unchanged |
| Hierarchy: parent card + bare `<h3>` + children table + button below | Hierarchy (entity-card): "Parent Organization" + "Child Organizations" subsections using the §15 entity card subsection layout |
| Addresses (standalone section) | Removed — moved into Contact Information as "Locations" subsection |
| Contact Methods (standalone section) | Removed — split into "Email Addresses" + "Phone Numbers" subsections within Contact Information |
| — | **Contact Information** (new `entity-section` + `entity-card`) with three subsections: Email Addresses, Phone Numbers, Locations |
| Links — `+ Add` button below table | Links — `+ Add` button in subsection header row (label left, button right), per §15 pattern |
| Identifiers — `+ Add` button below table | Identifiers — same |
| Roles (read-only filter table) | Roles — no change |

### 2. Contact Information — backend

**`orgs.py` detail handler:**
- Split single `contacts` fetch into `email_contacts` + `phone_contacts` (filter by `contact_type` in Python or SQL).
- Rename `addresses` → keep as-is; pass to template for the Locations subsection.

**`orgs_contacts.py` new-row route:**
- Accept `contact_type` query param (`email` | `phone`).
- Pass `contact_type` to the template as a context variable.
- The form renders it as a `<input type="hidden" name="contact_type" value="{{ contact_type }}">`.
- Type is fixed at creation — not editable in the edit form (shown as a read-only label instead of the `contact_type` select).

**Form row IDs:**
- New rows: `email-row-new` / `phone-row-new` (avoids DOM ID collision if both forms open simultaneously).
- Edit rows: `contact-row-{{ c.id }}` — unchanged.

**New-row buttons in template:**
```html
hx-get="/admin/orgs/{{ org.id }}/contacts/new-row/?contact_type=email"
hx-target="#emails-table tbody"
hx-swap="afterbegin"
```

**Create POST target:** `hx-target="#email-row-new"` / `#phone-row-new` with `hx-swap="outerHTML"` — replaces the inline new-row form with the new read row. Non-HTMX: `RedirectResponse` to detail page (unchanged).

### 3. Form row consistency

Apply AGENTS.md "row form input styling" pattern to all remaining form rows:

**All sections (addresses, contacts, links, identifiers, children):**
- Wrap all `<input>` and `<select>` elements in `<div class="form-group" style="margin-bottom:0">`.
- Primary input gets `flex:1`.
- Save + Cancel wrapped in `<div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">`.

**`_link_form_row.html` specifically:**
- Replace bare `<label><input type="checkbox" name="is_active">` with `.toggle` pill component (no auto-save — form field only, no `hx-post`).
- Replace bare `<label><input type="checkbox" name="is_canonical">` with `.toggle` pill component (same).
- Remove the `flex-wrap:wrap` on the form — use a two-row layout if needed, or keep flex with the inputs getting `min-width`.

**`_contact_form_row.html`:**
- Remove the `contact_type` `<select>` (type is now fixed at creation).
- New-row: render `<input type="hidden" name="contact_type" value="{{ contact_type }}">`.
- Edit-row: show type as a read-only `<span class="badge">` or muted label — no editable field.

### 4. Hierarchy section restructure

```
<section class="entity-section">
  <h2>Hierarchy</h2>
  <div class="entity-card">
    <!-- Parent Organization subsection -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin:0 0 var(--space-3)">
      <h3 class="field-group-label">Parent Organization</h3>
    </div>
    {% include "admin/orgs/partials/_parent_read.html" %}

    <!-- Child Organizations subsection -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin:var(--space-5) 0 var(--space-3)">
      <h3 class="field-group-label">Child Organizations</h3>
      <button class="btn btn--sm btn--secondary" ...>+ Add child</button>
    </div>
    <div class="table-wrapper">
      <table id="children-table" ...>...</table>
    </div>
  </div>
</section>
```

### 5. Contact Information section structure

```
<section class="entity-section">
  <h2>Contact Information</h2>
  <div class="entity-card">
    <!-- Email Addresses -->
    <div style="display:flex;..."><h3 class="field-group-label">Email Addresses</h3><button ...>+ Add email</button></div>
    <div class="table-wrapper"><table id="emails-table" ...> ... </table></div>

    <!-- Phone Numbers -->
    <div style="display:flex;...;margin-top:var(--space-5)"><h3 class="field-group-label">Phone Numbers</h3><button ...>+ Add phone</button></div>
    <div class="table-wrapper"><table id="phones-table" ...> ... </table></div>

    <!-- Locations -->
    <div style="display:flex;...;margin-top:var(--space-5)"><h3 class="field-group-label">Locations</h3><button ...>+ Add location</button></div>
    <div class="table-wrapper"><table id="addresses-table" ...> ... </table></div>
  </div>
</section>
```

---

## Out of Scope

- People / Roles / Role-Assignments detail pages
- Danger Zone restructure
- Roles section (read-only filter table)
- STYLE.md updates — deferred to end of work session
- Enabling `contact_type` editing after creation (type is immutable once set)
