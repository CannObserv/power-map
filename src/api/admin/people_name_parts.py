"""Helpers for `person_name_parts` (sidecar to person_names, 1:0..1).

Phase 2d (#123) introduced standalone POST `/parts/` and POST
`/parts/delete/` routes inside this module. Issue #127 removed those
routes: the parts upsert / delete is now performed inside the unified
`/edit-row/` and `/` (create) handlers in `_names_shared.py`, gated by
`supports_person_metadata=True`. This module now exports just the
validation helpers and the combined upsert-or-delete coroutine that
those handlers call inside their existing transaction.

Public surface:

- `ARRAY_CAP` — per-array element cap (mirrors prior route validation).
- `upsert_or_delete_parts(db, *, name_id, …)` — runs cap + allowlist
  validation, then either INSERT … ON CONFLICT DO UPDATE or
  DELETE-when-all-empty against `person_name_parts`. Returns the
  validation error message (or None on success). Caller is responsible
  for raising / rolling back when the return value is non-None.
"""

# Mirror person_name_parts.primary_identifier CHECK constraint.
_PRIMARY_IDENTIFIERS: tuple[str, ...] = ("family", "given", "patronymic", "mononym")

ARRAY_CAP = 5


def _trim_array(values: list[str] | None) -> list[str]:
    """Strip whitespace, drop empty entries, preserve order."""
    if not values:
        return []
    return [v.strip() for v in values if v and v.strip()]


async def upsert_or_delete_parts(
    db,
    *,
    name_id: str,
    given_names: list[str] | None,
    family_names: list[str] | None,
    additional_names: list[str] | None,
    honorific_prefix: str | None,
    honorific_suffix: str | None,
    primary_identifier: str | None,
) -> str | None:
    """Upsert (or delete-if-all-empty) the parts row for `name_id`.

    Returns the validation error message, or ``None`` on success. When
    non-None the caller should surface it as a form error and roll back
    the surrounding transaction.

    Cap check runs against the raw input arrays — empty entries
    contribute to the cap so the user-facing message reflects what they
    typed, not the post-trim count.
    """
    for label, vals in (
        ("given_names", given_names or []),
        ("family_names", family_names or []),
        ("additional_names", additional_names or []),
    ):
        if len(vals) > ARRAY_CAP:
            return f"{label}: no more than {ARRAY_CAP} entries (got {len(vals)})."

    given = _trim_array(given_names)
    family = _trim_array(family_names)
    additional = _trim_array(additional_names)
    pre = (honorific_prefix or "").strip() or None
    suf = (honorific_suffix or "").strip() or None
    pi_raw = (primary_identifier or "").strip()
    if pi_raw and pi_raw not in _PRIMARY_IDENTIFIERS:
        allowed = ", ".join(_PRIMARY_IDENTIFIERS)
        return f"primary_identifier must be one of: {allowed} (got {pi_raw!r})."
    pi: str | None = pi_raw or None

    has_any = bool(given or family or additional or pre or suf or pi)
    if not has_any:
        # Idempotent delete — issue #127 semantic flip. If the row never
        # existed this is a no-op; if it existed, it's now gone.
        await db.execute(
            "DELETE FROM person_name_parts WHERE person_name_id=$1",
            name_id,
        )
        return None

    await db.execute(
        "INSERT INTO person_name_parts ("
        "  person_name_id, given_names, family_names, additional_names,"
        "  honorific_prefix, honorific_suffix, primary_identifier"
        ") VALUES ($1, $2, $3, $4, $5, $6, $7)"
        " ON CONFLICT (person_name_id) DO UPDATE SET"
        "   given_names      = EXCLUDED.given_names,"
        "   family_names     = EXCLUDED.family_names,"
        "   additional_names = EXCLUDED.additional_names,"
        "   honorific_prefix = EXCLUDED.honorific_prefix,"
        "   honorific_suffix = EXCLUDED.honorific_suffix,"
        "   primary_identifier = EXCLUDED.primary_identifier",
        name_id,
        given or None,
        family or None,
        additional or None,
        pre,
        suf,
        pi,
    )
    return None
