# Contact Normalizers — Admin UI

**Date:** 2026-03-26
**Issue:** #43
**Scope:** `orgs_contacts.py`, `_contact_form_row.html`, tests

## Goal

Wire existing normalizers into the admin contact create/edit routes so that:
- Email addresses are validated and domain-lowercased before storage
- Phone numbers are normalized to E.164 before storage
- Validation errors surface inline in the form row (not bare 422s)

## Approved Approach (Option D)

### Template (`_contact_form_row.html`)

- `type="email"` on the value input when `contact_type == 'email'` — HTML5 browser hint
- Form layout changes to `display:grid` so an error alert can sit above the inputs
- All current flex content moves into a nested `<div style="display:flex;...">`
- Error alert: `{% if error %}<div class="alert alert--error" role="alert">{{ error }}</div>{% endif %}`
- Value pre-population: `value_input` context var takes precedence over `c.value` — used to
  repopulate the field with what the user typed after a validation error

### Route (`orgs_contacts.py`)

Module-level normalizer instances (constructed once):
```python
_email_normalizer = EmailNormalizer()
_phone_normalizer = PhoneNormalizer()
```

In `contact_create` and `contact_edit_row_post`, after stripping `value`:
```python
try:
    if contact_type == "email":
        value = _email_normalizer.normalize(value).value
    elif contact_type == "phone":
        value = _phone_normalizer.normalize(value).value
except ValueError:
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_contact_form_row.html",
        {
            "org_id": org_id,
            "c": existing_or_none,
            "contact_type": contact_type,
            "value_input": raw_value,
            "error": "Enter a valid email address."
                     if contact_type == "email"
                     else "Enter a valid phone number (e.g. (206) 555-1234 or +12065551234).",
        },
    )
```

Error response uses HTTP 200 so HTMX performs the swap and the inline error is displayed.

## Key Decisions

- **200 on error** — HTMX only swaps on 2xx by default; returning 422 would silently no-op the swap.
- **User-friendly error messages** — not the raw `ValueError` string from the normalizer.
- **`value_input` optional var** — avoids changing all non-error render paths; Jinja2 `is defined` test handles absence gracefully.
- **No label repopulation** — `display_label` is low-stakes; losing it on a validation error is acceptable.
- **Phone: silent normalization on success** — user types `(206) 555-1234`, stored as `+12065551234`; flash confirms the stored value.

## Out of Scope

- Retroactive normalization of existing rows
- People admin contacts
- Address normalization (tracked in #42)
