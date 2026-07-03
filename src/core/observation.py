"""Core observation service: identifier-based entity match or create + per-surface writers."""

import json
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from src.api.public.schemas import ObservationAcronym, ObservationOrgName, ObservationPersonName

from src.core.db import generate_id
from src.core.logging import get_logger
from src.core.normalizers.address import get_address_normalizer
from src.core.normalizers.email import EmailNormalizer
from src.core.normalizers.phone import PhoneNormalizer
from src.core.organizations import ActiveOnArchivedOrg, OrgNotFound, set_org_active
from src.core.types import EVENT_PLACE_PRECISIONS

logger = get_logger(__name__)

_email_normalizer = EmailNormalizer()
_phone_normalizer = PhoneNormalizer()


class Disposition(StrEnum):
    AUTO_ATTACHED = "auto-attached"
    NEW = "new"
    REJECTED = "rejected"


class ObservationRejected(Exception):
    """Raised by attribute writers when the observation payload fails validation."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class IdentifierConflict(Exception):
    """Raised when an additional identifier conflicts with an existing one on the entity."""

    def __init__(self, identifier_type_slug: str) -> None:
        self.identifier_type_slug = identifier_type_slug
        super().__init__(f"Identifier conflict on type {identifier_type_slug!r}")


# ---------------------------------------------------------------------------
# resolve_entity (Step 5)
# ---------------------------------------------------------------------------


async def resolve_entity(
    conn,
    identifier_type_slug: str,
    identifier_value: str,
    *,
    create_data: dict | None = None,
) -> tuple[str, str, Disposition, str | None]:
    """Find or create the entity identified by the given identifier.

    Returns (entity_id, entity_type, disposition, reason).

    disposition is:
      - AUTO_ATTACHED  if an existing identifier row was found
      - NEW            if a new entity + identifier row were created
      - REJECTED       if the identifier_type_slug is unknown, or if the entity
                       type requires create_data for NEW and none was provided

    reason is a human-readable string on REJECTED, None otherwise.
    Raises nothing — REJECTED is returned, not raised.
    """
    eit = await conn.fetchrow(
        "SELECT id, entity_type, is_internal FROM entity_identifier_types WHERE slug = $1",
        identifier_type_slug,
    )
    if eit is None:
        logger.warning("Unknown identifier_type_slug=%r", identifier_type_slug)
        return "", "", Disposition.REJECTED, f"unknown_identifier_type: {identifier_type_slug!r}"

    entity_type = eit["entity_type"]

    if eit["is_internal"]:
        # PM-native lookup: bypass identifiers table, query entity row directly.
        # Never NEW — a pm_* type cannot create entities.
        entity_id = await _lookup_entity_by_pm_id(conn, entity_type, identifier_value)
        if entity_id is None:
            logger.warning("pm-internal resolve: %s id=%r not found", entity_type, identifier_value)
            return "", "", Disposition.REJECTED, f"pm_id_not_found: {identifier_value!r}"
        return entity_id, entity_type, Disposition.AUTO_ATTACHED, None

    entity_identifier_type_id = eit["id"]
    existing = await conn.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id = $1 AND value = $2",
        entity_identifier_type_id,
        identifier_value,
    )
    if existing:
        return existing["entity_id"], entity_type, Disposition.AUTO_ATTACHED, None

    if entity_type == "jurisdiction":
        if not create_data:
            logger.warning(
                "Jurisdiction NEW disposition requires create_data; identifier_type=%r",
                identifier_type_slug,
            )
            return "", "", Disposition.REJECTED, "jurisdiction_new_requires_create_data"
        # Pre-validate type_slug outside the transaction so we can return REJECTED cleanly.
        type_row = await conn.fetchrow(
            "SELECT id FROM jurisdiction_types WHERE slug=$1", create_data["type_slug"]
        )
        if type_row is None:
            logger.warning("Unknown jurisdiction_type_slug=%r", create_data["type_slug"])
            return (
                "",
                "",
                Disposition.REJECTED,
                f"unknown_jurisdiction_type: {create_data['type_slug']!r}",
            )
        create_data = {**create_data, "type_id": type_row["id"]}

    # Resolved once per call; entity_identifier_types is append-only after apply_schema.
    jur_slug_type_id: str | None = None
    if entity_type == "jurisdiction" and identifier_type_slug != "jur_slug":
        jur_slug_eit = await conn.fetchrow(
            "SELECT id FROM entity_identifier_types WHERE slug = 'jur_slug'"
        )
        if jur_slug_eit is None:
            logger.warning(
                "jur_slug identifier type not found — slug auto-registration skipped"
                " for identifier_type=%r",
                identifier_type_slug,
            )
        else:
            jur_slug_type_id = jur_slug_eit["id"]

    try:
        async with conn.transaction():
            entity_id = await _create_entity(conn, entity_type, create_data=create_data)
            await conn.execute(
                "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                entity_id,
                entity_identifier_type_id,
                identifier_value,
            )
            if jur_slug_type_id is not None:
                await conn.execute(
                    "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                    " VALUES ($1, $2, $3, $4)",
                    generate_id(),
                    entity_id,
                    jur_slug_type_id,
                    create_data["slug"],
                )
    except asyncpg.UniqueViolationError:
        logger.warning(
            "UniqueViolation creating %s for identifier_type=%r value=%r",
            entity_type,
            identifier_type_slug,
            identifier_value,
        )
        return "", "", Disposition.REJECTED, "unique_violation"
    logger.info(
        "Created %s entity_id=%s for identifier_type=%s value=%r",
        entity_type,
        entity_id,
        identifier_type_slug,
        identifier_value,
    )
    return entity_id, entity_type, Disposition.NEW, None


_ENTITY_TABLE = {
    "organization": "organizations",
    "person": "people",
    "jurisdiction": "jurisdictions",
    "role_assignment": "role_assignments",
}


async def _lookup_entity_by_pm_id(conn, entity_type: str, entity_id: str) -> str | None:
    """Return entity_id if the row exists and is not archived, else None."""
    table = _ENTITY_TABLE.get(entity_type)
    if table is None:
        return None
    return await conn.fetchval(
        f"SELECT id FROM {table} WHERE id=$1 AND archived_at IS NULL",  # noqa: S608
        entity_id,
    )


async def _create_entity(conn, entity_type: str, *, create_data: dict | None = None) -> str:
    """Insert a minimal entity row and return its id."""
    entity_id = generate_id()
    if entity_type == "person":
        await conn.execute("INSERT INTO people (id) VALUES ($1)", entity_id)
    elif entity_type == "organization":
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", entity_id)
    elif entity_type == "jurisdiction":
        if not create_data:
            raise ValueError("jurisdiction _create_entity requires create_data")
        await conn.execute(
            "INSERT INTO jurisdictions"
            " (id, slug, name, type_id, valid_from, valid_until, notes)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            entity_id,
            create_data["slug"],
            create_data["name"],
            create_data["type_id"],
            create_data.get("valid_from"),
            create_data.get("valid_until"),
            create_data.get("notes"),
        )
    elif entity_type == "role_assignment":
        raise ValueError("Cannot create bare role_assignment entity from observation")
    else:
        raise ValueError(f"Unknown entity_type: {entity_type!r}")
    return entity_id


# ---------------------------------------------------------------------------
# Per-surface writers (Step 7)
#
# Governing principle: append-only, exact-match dedup, never overwrite.
# ---------------------------------------------------------------------------


async def write_names(
    conn,
    entity_id: str,
    entity_type: str,
    api_key_id: str,
    names: "Sequence[ObservationPersonName] | Sequence[ObservationOrgName]",
) -> None:
    """Write name claims to person_names or organization_names.

    Policy:
      - Append if no exact (entity_id, name) match
      - visibility='public' (person_names only)
      - source_key_id = api_key_id on new name rows
      - parts: write on new name row; on existing row write only if parts row absent
      - is_canonical hint: honoured only if no canonical already exists for that
        (person_id, name_type) slot (person) or the whole org; never displaces.
    """
    if entity_type == "person":
        for n in names:
            existing = await conn.fetchrow(
                "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
                entity_id,
                n.name,
            )
            if existing:
                name_id = existing["id"]
                is_new = False
            else:
                is_new = True
                name_id = generate_id()
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO person_names"
                        " (id, person_id, name, name_type, locale, script, sort_as,"
                        "  visibility, source_key_id, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6, $7, 'public', $8,"
                        "   ($9 AND NOT EXISTS ("
                        "     SELECT 1 FROM person_names"
                        "     WHERE person_id = $2 AND name_type = $4 AND is_canonical = TRUE"
                        "   )))",
                        name_id,
                        entity_id,
                        n.name,
                        n.name_type,
                        n.locale,
                        n.script,
                        n.sort_as,
                        api_key_id,
                        n.is_canonical,
                    )
                    if n.parts is not None:
                        await _write_person_name_parts(conn, name_id, n.parts, is_new=True)
            if n.parts is not None and not is_new:
                await _write_person_name_parts(conn, name_id, n.parts, is_new=False)
    elif entity_type == "organization":
        # If any name carries the canonical hint, only that name is eligible for promotion.
        # Otherwise fall through to first-wins auto-promotion (NOT EXISTS guard).
        canonical_hint = next((n.name for n in names if n.is_canonical), None)
        for n in names:
            existing = await conn.fetchrow(
                "SELECT id FROM organization_names WHERE organization_id=$1 AND name=$2",
                entity_id,
                n.name,
            )
            if existing:
                continue
            # eligible=True → this name may try to claim canonical via NOT EXISTS.
            # When a hint is present, only the hinted name is eligible.
            # When no hint, every name is eligible (first insert wins via NOT EXISTS).
            eligible = (canonical_hint is None) or (n.name == canonical_hint)
            try:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO organization_names"
                        " (id, organization_id, name, name_type, source_key_id,"
                        "  effective_start, effective_end, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6, $7,"
                        "   ($8 AND NOT EXISTS (SELECT 1 FROM organization_names"
                        "               WHERE organization_id = $2 AND is_canonical = TRUE)))",
                        generate_id(),
                        entity_id,
                        n.name,
                        n.name_type,
                        api_key_id,
                        n.effective_start,
                        n.effective_end,
                        eligible,
                    )
            except asyncpg.exceptions.UniqueViolationError:
                # Concurrent write promoted a different name first; insert without canonical.
                await conn.execute(
                    "INSERT INTO organization_names"
                    " (id, organization_id, name, name_type, source_key_id,"
                    "  effective_start, effective_end)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    generate_id(),
                    entity_id,
                    n.name,
                    n.name_type,
                    api_key_id,
                    n.effective_start,
                    n.effective_end,
                )
    elif entity_type == "jurisdiction":
        pass  # jurisdiction name lives on the row; no names table
    else:
        raise ValueError(f"write_names: unsupported entity_type {entity_type!r}")


async def _write_person_name_parts(conn, name_id: str, parts, *, is_new: bool) -> None:
    """Insert person_name_parts row. On new name row → write unconditionally.
    On existing row → write only if no parts row already exists (write-if-null).
    """
    if not is_new:
        existing = await conn.fetchrow(
            "SELECT person_name_id FROM person_name_parts WHERE person_name_id=$1",
            name_id,
        )
        if existing:
            return
    await conn.execute(
        "INSERT INTO person_name_parts"
        " (person_name_id, given_names, family_names, additional_names,"
        "  honorific_prefix, honorific_suffix, primary_identifier)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        name_id,
        list(parts.given_names) or None,
        list(parts.family_names) or None,
        list(parts.additional_names) or None,
        parts.honorific_prefix,
        parts.honorific_suffix,
        parts.primary_identifier,
    )


async def write_links(conn, entity_id: str, entity_type: str, links: list) -> None:
    """Write link claims. Resolves link_type_slug → id if needed.

    Dedup on (entity_type, entity_id, url, link_type_id).
    """
    for link in links:
        link_type_id = link.link_type_id
        if link_type_id is None:
            row = await conn.fetchrow(
                "SELECT id FROM link_types WHERE slug=$1", link.link_type_slug
            )
            if row is None:
                raise ObservationRejected(f"Unknown link_type_slug: {link.link_type_slug!r}")
            link_type_id = row["id"]
        existing = await conn.fetchrow(
            "SELECT id FROM links"
            " WHERE entity_type=$1 AND entity_id=$2 AND url=$3 AND link_type_id=$4",
            entity_type,
            entity_id,
            link.url,
            link_type_id,
        )
        if existing:
            continue
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, $2, $3, $4, $5)",
                generate_id(),
                entity_type,
                entity_id,
                link.url,
                link_type_id,
            )
            await _record_entity_change(conn, entity_type, entity_id)


async def _record_entity_change(conn, entity_type: str, entity_id: str) -> None:
    """Append an 'updated' outbox row; callers own any surrounding transaction."""
    await conn.execute(
        "INSERT INTO entity_changes (entity_type, entity_id, change_kind)"
        " VALUES ($1, $2, 'updated')",
        entity_type,
        entity_id,
    )


async def _null_fill_metadata(
    conn,
    entity_type: str,
    entity_id: str,
    table: str,
    col: str,
    pk_val: str,
    value: str,
    record_change: bool = True,
) -> None:
    """Fill col in table where pk_val row has NULL, atomically with an entity_changes row.

    table/col are caller-controlled string constants, not user input.
    Pass record_change=False for tables with a touch-parent trigger
    (entity_addresses) — the trigger already emits the outbox row.
    """
    async with conn.transaction():
        updated = await conn.fetchval(
            f"UPDATE {table} SET {col}=$1 WHERE id=$2 AND {col} IS NULL RETURNING id",
            value,
            pk_val,
        )
        if updated and record_change:
            await _record_entity_change(conn, entity_type, entity_id)


async def write_contact_methods(
    conn, entity_id: str, entity_type: str, contact_methods: list
) -> None:
    """Normalise and write contact method claims.

    Raises ObservationRejected on bad format. Dedup on
    (entity_type, entity_id, contact_type, value) after normalisation.
    """
    for cm in contact_methods:
        try:
            if cm.contact_type == "email":
                normalized = _email_normalizer.normalize(cm.value).value
            elif cm.contact_type == "phone":
                normalized = _phone_normalizer.normalize(cm.value).value
            else:
                raise ObservationRejected(f"Unsupported contact_type: {cm.contact_type!r}")
        except ValueError as exc:
            raise ObservationRejected(str(exc)) from exc
        if normalized is None:
            raise ObservationRejected(f"Empty contact value for type {cm.contact_type!r}")
        existing = await conn.fetchrow(
            "SELECT id FROM contact_methods"
            " WHERE entity_type=$1 AND entity_id=$2 AND contact_type=$3 AND value=$4",
            entity_type,
            entity_id,
            cm.contact_type,
            normalized,
        )
        if existing:
            if cm.display_label:
                await _null_fill_metadata(
                    conn,
                    entity_type,
                    entity_id,
                    "contact_methods",
                    "display_label",
                    existing["id"],
                    cm.display_label,
                )
            continue
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO contact_methods"
                " (id, entity_type, entity_id, contact_type, value, display_label)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                generate_id(),
                entity_type,
                entity_id,
                cm.contact_type,
                normalized,
                cm.display_label,
            )
            await _record_entity_change(conn, entity_type, entity_id)


async def write_addresses(conn, entity_id: str, entity_type: str, addresses: list) -> None:
    """Write address claims via the address normalizer.

    Dedup mirrors the DB unique key on ``entity_addresses`` — the normalized form
    plus the validity window ``(valid_from, valid_until)`` (#256):

    - **Dated claim** (either window bound supplied): strict window equality via
      ``IS NOT DISTINCT FROM``. A new window of an address the entity already has
      is history, not a duplicate — a fresh link is written, reusing the existing
      ``addresses`` row rather than inserting a per-window duplicate.
    - **Dateless claim** (both bounds NULL): matches *any* existing row regardless
      of window (unchanged behavior). Admin end-dating stays authoritative — a
      dateless re-observation never resurrects a closed window
      (docs/CONVENTIONS.md §"Address validity windows (#181)", #256 decision).

    Raises ObservationRejected on normalizer failure.
    """
    normalizer = get_address_normalizer()
    for addr in addresses:
        try:
            result = await normalizer.normalize(addr.raw_input)
        except Exception as exc:
            raise ObservationRejected(f"Address normalization failed: {exc}") from exc
        if result.skipped:
            raise ObservationRejected(
                f"Address skipped by normalizer (unrecognised format): {addr.raw_input!r}"
            )
        if result.value is None:
            raise ObservationRejected(
                f"Address normalisation returned no result for: {addr.raw_input!r}"
            )
        v = result.value
        # Dedup key: standardized form if present, else raw_input
        dedup_form = v.get("standardized") or v.get("raw_input") or addr.raw_input
        dated = addr.valid_from is not None or addr.valid_until is not None
        # Dated claims match on the exact window; dateless claims match any window.
        window_clause = (
            "   AND ea.valid_from IS NOT DISTINCT FROM $5"
            "   AND ea.valid_until IS NOT DISTINCT FROM $6"
            if dated
            else ""
        )
        params = [entity_type, entity_id, addr.address_type, dedup_form]
        if dated:
            params += [addr.valid_from, addr.valid_until]
        existing = await conn.fetchrow(
            "SELECT ea.id FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_type=$1 AND ea.entity_id=$2 AND ea.address_type=$3"
            "   AND COALESCE(a.standardized, a.raw_input) = $4" + window_clause,
            *params,
        )
        if existing:
            if addr.display_name:
                # trg_touch_entity_on_address_change emits the outbox row (#181)
                await _null_fill_metadata(
                    conn,
                    entity_type,
                    entity_id,
                    "entity_addresses",
                    "display_name",
                    existing["id"],
                    addr.display_name,
                    record_change=False,
                )
            continue
        # No exact (form + window) match. Reuse an existing addresses row for the
        # same entity + type + form in any window so a new window does not spawn a
        # duplicate physical address; only mint a fresh row when the form is new.
        reuse = await conn.fetchrow(
            "SELECT ea.address_id FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_type=$1 AND ea.entity_id=$2 AND ea.address_type=$3"
            "   AND COALESCE(a.standardized, a.raw_input) = $4"
            " ORDER BY ea.created_at, ea.id"
            " LIMIT 1",
            entity_type,
            entity_id,
            addr.address_type,
            dedup_form,
        )
        components_val = v.get("components")
        components_str = json.dumps(components_val) if components_val else None
        async with conn.transaction():
            if reuse:
                aid = reuse["address_id"]
            else:
                aid = generate_id()
                await conn.execute(
                    "INSERT INTO addresses"
                    " (id, raw_input, address_line_1, address_line_2, city, region,"
                    "  postal_code, country, standardized, latitude, longitude, components)"
                    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                    aid,
                    v.get("raw_input") or addr.raw_input,
                    v.get("address_line_1"),
                    v.get("address_line_2"),
                    v.get("city"),
                    v.get("region"),
                    v.get("postal_code"),
                    v.get("country") or "US",
                    v.get("standardized"),
                    v.get("latitude"),
                    v.get("longitude"),
                    components_str,
                )
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type, display_name,"
                "  valid_from, valid_until)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                generate_id(),
                entity_type,
                entity_id,
                aid,
                addr.address_type,
                addr.display_name,
                addr.valid_from,
                addr.valid_until,
            )
            # trg_touch_entity_on_address_change emits the outbox row (#181)


async def write_org_acronyms(
    conn, organization_id: str, acronyms: "list[ObservationAcronym]"
) -> None:
    """Append acronyms. Dedup on exact string. Auto-promotes first to canonical.

    If any entry carries is_canonical=True, only that entry is eligible for
    promotion; all others land as non-canonical.  When no hint is given, the
    first new acronym wins (NOT EXISTS guard), matching prior behaviour.
    """
    canonical_hint = next((a.acronym for a in acronyms if a.is_canonical), None)
    for a in acronyms:
        existing = await conn.fetchrow(
            "SELECT id FROM organization_acronyms WHERE organization_id=$1 AND acronym=$2",
            organization_id,
            a.acronym,
        )
        if existing:
            continue
        eligible = (canonical_hint is None) or (a.acronym == canonical_hint)
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                    " VALUES ($1, $2, $3,"
                    "   ($4 AND NOT EXISTS (SELECT 1 FROM organization_acronyms"
                    "               WHERE organization_id = $2 AND is_canonical = TRUE)))",
                    generate_id(),
                    organization_id,
                    a.acronym,
                    eligible,
                )
        except asyncpg.exceptions.UniqueViolationError:
            # Concurrent write promoted a different acronym first; insert without canonical.
            await conn.execute(
                "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, $3, FALSE)",
                generate_id(),
                organization_id,
                a.acronym,
            )


async def resolve_role(
    conn,
    organization_id: str,
    title: str,
    *,
    notes: str | None = None,
    established_on: date | None = None,
    abolished_on: date | None = None,
) -> tuple[str, Disposition, str | None]:
    """Match or create a role by (organization_id, lower(title)).

    Returns (role_id, disposition, reason).
    disposition is AUTO_ATTACHED if an active (non-archived) match is found,
    NEW if created, REJECTED if the organization_id does not exist.
    reason is a human-readable string on REJECTED, None otherwise.
    """
    org_exists = await conn.fetchval(
        "SELECT 1 FROM organizations WHERE id=$1 AND archived_at IS NULL", organization_id
    )
    if not org_exists:
        logger.warning("resolve_role: unknown organization_id=%r", organization_id)
        return "", Disposition.REJECTED, f"org_not_found: {organization_id!r}"

    existing = await conn.fetchrow(
        "SELECT id FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
        " AND archived_at IS NULL",
        organization_id,
        title,
    )
    if existing:
        return existing["id"], Disposition.AUTO_ATTACHED, None

    role_id = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title, notes, established_on, abolished_on)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        role_id,
        organization_id,
        title,
        notes,
        established_on,
        abolished_on,
    )
    logger.info("Created role id=%s org=%s title=%r", role_id, organization_id, title)
    return role_id, Disposition.NEW, None


async def write_role_assignments(conn, person_id: str, role_assignments: list) -> None:
    """Append role assignments. No-op if open (no end_date) assignment exists for same role."""
    for ra in role_assignments:
        open_existing = await conn.fetchrow(
            "SELECT id FROM role_assignments"
            " WHERE person_id=$1 AND role_id=$2 AND end_date IS NULL"
            "   AND archived_at IS NULL",
            person_id,
            ra.role_id,
        )
        if open_existing:
            continue
        start_date = date.fromisoformat(ra.start_date) if ra.start_date else None
        end_date = date.fromisoformat(ra.end_date) if ra.end_date else None
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            person_id,
            ra.role_id,
            start_date,
            end_date,
        )


async def write_org_parent(conn, organization_id: str, parent_id: str) -> None:
    """Set organizations.parent_id if currently NULL (write-if-null)."""
    await conn.execute(
        "UPDATE organizations SET parent_id=$1 WHERE id=$2 AND parent_id IS NULL",
        parent_id,
        organization_id,
    )


async def write_org_active(conn, organization_id: str, active: bool) -> None:
    """Set an organization's active flag from an observation (#240).

    The active axis is orthogonal to archived_at, but an archived org is not a
    valid observation target: archiving is an admin lifecycle gate, so asserting
    active on an archived row is treated as a malformed observation and rejected.

    Delegates the archived + no-op guards to the shared core helper
    ``set_org_active`` (#241), mapping its rejections onto the observation-domain
    ``ObservationRejected``. The caller (resolve_entity) guarantees the org
    exists and runs inside the observation transaction, so the helper's
    ``FOR UPDATE`` lock is held until commit; the OrgNotFound mapping is a
    defensive belt against a concurrent hard-delete in that window.
    """
    try:
        await set_org_active(conn, organization_id, active)
    except ActiveOnArchivedOrg as exc:
        raise ObservationRejected("active_on_archived_org") from exc
    except OrgNotFound as exc:
        raise ObservationRejected("org_not_found") from exc


async def write_org_jurisdiction_affiliations(
    conn, organization_id: str, affiliations: list
) -> None:
    """Insert org-jurisdiction affiliation rows (idempotent).

    Raises ObservationRejected if an affiliation_type_slug is not found.
    FK violations on jurisdiction_id propagate as asyncpg.ForeignKeyViolationError.
    """
    if not affiliations:
        return
    for aff in affiliations:
        type_id = await conn.fetchval(
            "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug = $1",
            aff.affiliation_type_slug,
        )
        if type_id is None:
            raise ObservationRejected(
                f"Unknown affiliation_type_slug: {aff.affiliation_type_slug!r}"
            )
        await conn.execute(
            """
            INSERT INTO organization_jurisdiction_affiliations
                (id, organization_id, jurisdiction_id, affiliation_type_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (organization_id, jurisdiction_id, affiliation_type_id) DO NOTHING
            """,
            generate_id(),
            organization_id,
            aff.jurisdiction_id,
            type_id,
        )


async def write_pronouns(conn, person_id: str, pronouns: str) -> None:
    """Set people.personal_pronouns if currently NULL (write-if-null)."""
    await conn.execute(
        "UPDATE people SET personal_pronouns=$1 WHERE id=$2 AND personal_pronouns IS NULL",
        pronouns,
        person_id,
    )


async def lookup_org_parent_by_name(conn, name: str) -> str:
    """Return id of the single active org whose canonical name matches.

    Raises ObservationRejected on zero or multiple matches.
    """
    rows = await conn.fetch(
        """
        SELECT o.id FROM organizations o
        JOIN organization_names n ON n.organization_id = o.id
        WHERE n.name = $1 AND n.is_canonical = TRUE AND o.archived_at IS NULL
        """,
        name,
    )
    if len(rows) != 1:
        raise ObservationRejected(
            f"Org parent name lookup returned {len(rows)} matches (expected 1)"
        )
    return rows[0]["id"]


async def lookup_org_parent_by_acronym(conn, acronym: str) -> str:
    """Return id of the single active org whose canonical acronym matches.

    Raises ObservationRejected on zero or multiple matches.
    """
    rows = await conn.fetch(
        """
        SELECT o.id FROM organizations o
        JOIN organization_acronyms a ON a.organization_id = o.id
        WHERE a.acronym = $1 AND a.is_canonical = TRUE AND o.archived_at IS NULL
        """,
        acronym,
    )
    if len(rows) != 1:
        raise ObservationRejected(
            f"Org parent acronym lookup returned {len(rows)} matches (expected 1)"
        )
    return rows[0]["id"]


async def write_additional_identifiers(conn, entity_id: str, additional_identifiers: list) -> None:
    """Write additional identifier claims.

    Each item must expose ``.identifier_type_slug`` and ``.identifier_value``.

    Policy:
      - Same type + same value on entity → no-op
      - Same type + different value on entity → raise IdentifierConflict
      - Unknown type slug → raise ObservationRejected
      - New type → insert
    """
    for item in additional_identifiers:
        slug = item.identifier_type_slug
        value = item.identifier_value
        eit = await conn.fetchrow(
            "SELECT id, is_internal FROM entity_identifier_types WHERE slug=$1", slug
        )
        if eit is None:
            raise ObservationRejected(f"Unknown identifier_type_slug: {slug!r}")
        if eit["is_internal"]:
            raise ObservationRejected(
                f"Internal identifier type {slug!r} cannot be assigned via observations"
            )
        eit_id = eit["id"]
        existing = await conn.fetchrow(
            "SELECT value FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
            entity_id,
            eit_id,
        )
        if existing is not None:
            if existing["value"] == value:
                continue
            raise IdentifierConflict(slug)
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            entity_id,
            eit_id,
            value,
        )


async def write_entity_events(
    conn: asyncpg.Connection,
    entity_id: str,
    entity_type: str,
    key_id: str | None,
    events: list,
) -> None:
    """Write entity event claims. Append-only with application-layer dedup.

    Dedup key: (event_type_id, event_year, event_month, event_day, event_hour,
    event_minute, event_second, linked_entity_id) — NULLs treated as equal.
    Validation:
    - applies_to mismatch → ObservationRejected
    - requires_year with no event_year → ObservationRejected
    - requires_linked_entity with no linked_entity_id → ObservationRejected
    """
    for ev in events:
        # Resolve slug → id (single query on slug path)
        event_type_id = ev.event_type_id
        if event_type_id is None:
            etype = await conn.fetchrow(
                "SELECT id, applies_to, requires_year, requires_linked_entity"
                " FROM entity_event_types WHERE slug=$1",
                ev.event_type_slug,
            )
            if etype is None:
                raise ObservationRejected(f"Unknown event_type_slug: {ev.event_type_slug!r}")
            event_type_id = etype["id"]
        else:
            etype = await conn.fetchrow(
                "SELECT applies_to, requires_year, requires_linked_entity"
                " FROM entity_event_types WHERE id=$1",
                event_type_id,
            )
            if etype is None:
                raise ObservationRejected(f"Unknown event_type_id: {event_type_id!r}")

        # applies_to check
        if etype["applies_to"] != "both" and etype["applies_to"] != entity_type:
            raise ObservationRejected(
                f"Event type {event_type_id!r} does not apply to {entity_type!r}"
            )

        # required field validation
        if etype["requires_year"] and ev.event_year is None:
            raise ObservationRejected(f"Event type {event_type_id!r} requires event_year")
        if etype["requires_linked_entity"] and not ev.linked_entity_id:
            raise ObservationRejected(f"Event type {event_type_id!r} requires linked_entity_id")

        # Validate event_place_address_id when provided
        place_addr_id = ev.event_place_address_id
        if place_addr_id:
            addr_row = await conn.fetchrow(
                "SELECT id, precision FROM addresses WHERE id=$1", place_addr_id
            )
            if addr_row is None:
                raise ObservationRejected(f"event_place_address_id {place_addr_id!r} not found")
            # NULL precision = pre-geocoding / historical record — allowed intentionally.
            if (
                addr_row["precision"] is not None
                and addr_row["precision"] not in EVENT_PLACE_PRECISIONS
            ):  # noqa: E501
                raise ObservationRejected(
                    f"event_place_address_id {place_addr_id!r} has precision "
                    f"'{addr_row['precision']}' — city, postal, or street required"
                )

        # Dedup: same event type + same partial date + same linked_entity_id = skip
        existing = await conn.fetchrow(
            """SELECT id FROM entity_events
               WHERE entity_id = $1
                 AND entity_type = $2
                 AND event_type_id = $3
                 AND event_year IS NOT DISTINCT FROM $4
                 AND event_month IS NOT DISTINCT FROM $5
                 AND event_day IS NOT DISTINCT FROM $6
                 AND event_hour IS NOT DISTINCT FROM $7
                 AND event_minute IS NOT DISTINCT FROM $8
                 AND event_second IS NOT DISTINCT FROM $9
                 AND linked_entity_id IS NOT DISTINCT FROM $10""",
            entity_id,
            entity_type,
            event_type_id,
            ev.event_year,
            ev.event_month,
            ev.event_day,
            ev.event_hour,
            ev.event_minute,
            ev.event_second,
            ev.linked_entity_id,
        )
        if existing:
            continue

        await conn.execute(
            """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day, event_hour, event_minute, event_second,
                event_place_text, event_place_address_id,
                linked_entity_type, linked_entity_id,
                notes, visibility, source_key_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)""",
            generate_id(),
            entity_type,
            entity_id,
            event_type_id,
            ev.event_year,
            ev.event_month,
            ev.event_day,
            ev.event_hour,
            ev.event_minute,
            ev.event_second,
            ev.event_place_text,
            place_addr_id,
            ev.linked_entity_type,
            ev.linked_entity_id,
            ev.notes,
            ev.visibility,
            key_id,
        )


async def resolve_assignment(
    conn,
    person_id: str,
    role_id: str,
    start_date: date | None,
    *,
    end_date: date | None = None,
    is_current: bool = False,
    notes: str | None = None,
) -> tuple[str, Disposition, str | None]:
    """Match or create a role assignment by (person_id, role_id, start_date).

    Returns (assignment_id, disposition, reason).
    disposition is AUTO_ATTACHED if an active (non-archived) match is found,
    NEW if created, REJECTED if person_id or role_id does not exist.
    reason is a human-readable string on REJECTED, None otherwise.
    """
    person_exists = await conn.fetchval(
        "SELECT 1 FROM people WHERE id=$1 AND archived_at IS NULL", person_id
    )
    if not person_exists:
        logger.warning("resolve_assignment: unknown person_id=%r", person_id)
        return "", Disposition.REJECTED, f"person_not_found: {person_id!r}"

    role_exists = await conn.fetchval(
        "SELECT 1 FROM roles WHERE id=$1 AND archived_at IS NULL", role_id
    )
    if not role_exists:
        logger.warning("resolve_assignment: unknown role_id=%r", role_id)
        return "", Disposition.REJECTED, f"role_not_found: {role_id!r}"

    existing = await conn.fetchrow(
        "SELECT id FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND start_date IS NOT DISTINCT FROM $3"
        "   AND archived_at IS NULL",
        person_id,
        role_id,
        start_date,
    )
    if existing:
        return existing["id"], Disposition.AUTO_ATTACHED, None

    assignment_id = generate_id()
    try:
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, start_date, end_date, is_current, notes)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7)",
            assignment_id,
            person_id,
            role_id,
            start_date,
            end_date,
            is_current,
            notes,
        )
    except asyncpg.UniqueViolationError:
        logger.warning(
            "UniqueViolation creating role_assignment person=%r role=%r start=%r",
            person_id,
            role_id,
            start_date,
        )
        return "", Disposition.REJECTED, "unique_violation"

    logger.info(
        "Created role_assignment id=%s person=%s role=%s start=%s",
        assignment_id,
        person_id,
        role_id,
        start_date,
    )
    return assignment_id, Disposition.NEW, None
