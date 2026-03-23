"""Multi-pass import pipeline coordinator."""

import csv
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from src.core.db import generate_id
from src.core.ingestion.base import ConfidenceRecord, value_hash
from src.core.ingestion.sources.csv_org import transform_org, validate_org
from src.core.ingestion.sources.csv_person import transform_person, validate_person
from src.core.ingestion.sources.csv_role import transform_role, validate_role
from src.core.logging import get_logger
from src.core.normalizers.address import AddressNormalizerConfig, FallbackAddressNormalizer

logger = get_logger(__name__)


@dataclass
class ImportConfig:
    """Configuration for a single import run.

    Address normalization:
        Addresses are always standardized via the external address-validator
        service when ADDRESS_VALIDATOR_API_KEY is set in the environment.
        Set validate_addresses=True (or VALIDATE_ADDRESSES=true env var) to
        also run the /validate endpoint for deliverability confirmation.
    """

    orgs_csv: Path
    people_csv: Path
    roles_csv: Path
    imported_by: str = "import"
    source_reliability: float = 0.8
    validate_addresses: bool = False
    notes: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.source_reliability <= 1.0:
            raise ValueError(
                f"source_reliability must be between 0.0 and 1.0, got {self.source_reliability}"
            )


@dataclass
class ReferenceData:
    """Lookup dicts loaded from DB at pipeline start."""

    link_type_ids: dict[str, str] = field(default_factory=dict)        # slug → id
    identifier_type_ids: dict[str, str] = field(default_factory=dict)  # slug → id


async def _load_reference_data(conn: asyncpg.Connection) -> ReferenceData:
    """Load reference lookup tables from the DB."""
    ref = ReferenceData()
    for row in await conn.fetch("SELECT id, slug FROM link_types"):
        ref.link_type_ids[row["slug"]] = row["id"]
    for row in await conn.fetch("SELECT id, slug FROM entity_identifier_types"):
        ref.identifier_type_ids[row["slug"]] = row["id"]
    return ref


def _build_address_normalizer(validate_addresses: bool) -> FallbackAddressNormalizer:
    """Build address normalizer from environment.

    Reads ADDRESS_VALIDATOR_API_KEY and VALIDATE_ADDRESSES from the environment.
    Always calls /standardize; calls /validate instead when validate_addresses
    is True or VALIDATE_ADDRESSES env var is set to a truthy value.
    Falls back to local usaddress parsing if the API key is absent.
    """
    api_key = os.environ.get("ADDRESS_VALIDATOR_API_KEY")
    if not api_key:
        logger.warning("ADDRESS_VALIDATOR_API_KEY not set; using local address parser")
        return FallbackAddressNormalizer()
    run_validation = validate_addresses or os.environ.get("VALIDATE_ADDRESSES", "").lower() in (
        "1", "true", "yes",
    )
    return FallbackAddressNormalizer(
        config=AddressNormalizerConfig(api_key=api_key, run_validation=run_validation)
    )


def _file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of the file at *path*."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of row dicts. Pydantic models handle stripping."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [
            {k: v for k, v in row.items() if k is not None}
            for row in csv.DictReader(f)
        ]


async def _write_provenance(
    conn: asyncpg.Connection,
    batch_id: str,
    source_row: int,
    entity_type: str,
    entity_id: str,
    action: str,
    raw: dict,
    errors: list | None = None,
) -> None:
    """Insert a row into import_provenance."""
    await conn.execute(
        """INSERT INTO import_provenance
               (id, batch_id, source_row, entity_type, entity_id, action, error_detail, raw_data)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        generate_id(), batch_id, source_row, entity_type, entity_id,
        action,
        json.dumps([{"field": e.field, "message": e.message} for e in errors])
        if errors else None,
        json.dumps(raw),
    )


async def _write_confidence(conn: asyncpg.Connection, records: list[ConfidenceRecord]) -> None:
    """Insert rows into field_confidence for each record."""
    for rec in records:
        await conn.execute(
            """INSERT INTO field_confidence
                   (id, entity_type, entity_id, field_name, value_hash,
                    source_reliability, validation_status, validation_detail, assessed_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            generate_id(), rec.entity_type, rec.entity_id, rec.field_name,
            value_hash(rec.normalized_value),
            rec.source_reliability, rec.validation_status,
            json.dumps(rec.validation_detail) if rec.validation_detail else None,
            rec.assessed_by,
        )


async def run_import(conn: asyncpg.Connection, config: ImportConfig) -> dict[str, Any]:
    """Run the full multi-pass import. Returns a summary dict."""
    start = time.monotonic()
    ref = await _load_reference_data(conn)
    addr_normalizer = _build_address_normalizer(config.validate_addresses)

    combined_hash = hashlib.sha256(
        (
            _file_hash(config.orgs_csv)
            + _file_hash(config.people_csv)
            + _file_hash(config.roles_csv)
        ).encode()
    ).hexdigest()

    org_rows = _read_csv(config.orgs_csv)
    person_rows = _read_csv(config.people_csv)
    role_rows = _read_csv(config.roles_csv)

    total_rows = len(org_rows) + len(person_rows) + len(role_rows)

    # Insert import_batches (idempotent: ON CONFLICT keeps the existing row).
    # Provenance rows reference batch_id via FK, so this must happen before
    # any entity processing. Counts are updated at the end of the run, but
    # only when loaded_count is 0 (i.e., first successful run).
    new_id = generate_id()
    batch_id: str = await conn.fetchval(
        """INSERT INTO import_batches
               (id, source_file, file_hash, imported_by,
                row_count, loaded_count, error_count, notes)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (file_hash) DO UPDATE SET id = import_batches.id
           RETURNING id""",
        new_id,
        f"{config.orgs_csv.name},{config.people_csv.name},{config.roles_csv.name}",
        combined_hash,
        config.imported_by,
        total_rows,
        0,
        0,
        config.notes,
    )

    summary: dict[str, Any] = {
        "batch_id": batch_id,
        "orgs_loaded": 0, "orgs_matched": 0, "orgs_error": 0,
        "people_loaded": 0, "people_matched": 0, "people_error": 0,
        "roles_loaded": 0, "roles_matched": 0, "roles_error": 0,
    }

    org_index: dict[str, str] = {}
    person_index: dict[str, str] = {}
    role_index: dict[tuple, str] = {}

    # -------------------------------------------------------------------------
    # Pass 1: Organizations
    # -------------------------------------------------------------------------
    for i, raw in enumerate(org_rows, start=2):
        result = validate_org(raw, source_row=i)
        if not result.ok:
            summary["orgs_error"] += 1
            for e in result.errors:
                logger.warning("org row %d field error: %s = %s", i, e.field, e.message)
            await _write_provenance(
                conn, batch_id, i, "organization", generate_id(), "error", raw, result.errors
            )
            continue
        result = await transform_org(
            result, org_index=org_index,
            source_reliability=config.source_reliability,
            address_normalizer=addr_normalizer,
        )
        for w in result.warnings:
            logger.warning("org row %d warning: %s", i, w)

        t = result.transformed
        name_lower = next(n["name"] for n in t["names"] if n["name_type"] == "legal").lower()

        # Dedup check
        existing_org = await conn.fetchrow(
            """SELECT o.id FROM organizations o
               JOIN organization_names n ON n.organization_id = o.id
               WHERE lower(n.name) = $1 AND n.is_canonical = true""",
            name_lower,
        )
        if existing_org:
            org_index[name_lower] = existing_org["id"]
            await _write_provenance(
                conn, batch_id, i, "organization", existing_org["id"], "matched", raw
            )
            summary["orgs_matched"] += 1
            continue

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
                t["org_id"], t["active"], t["parent_id"], t["notes"],
            )
            for n in t["names"]:
                await conn.execute(
                    "INSERT INTO organization_names"
                    " (id, organization_id, name, name_type, is_canonical)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), t["org_id"], n["name"], n["name_type"], n["is_canonical"],
                )
            if t.get("acronym"):
                await conn.execute(
                    "INSERT INTO organization_acronyms"
                    " (id, organization_id, acronym, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), t["org_id"], t["acronym"],
                )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods"
                    " (id, entity_type, entity_id, contact_type, value)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "organization", t["org_id"], cm["contact_type"], cm["value"],
                )
            for lnk in t["links"]:
                link_type_id = ref.link_type_ids.get(lnk["link_type_slug"])
                if link_type_id:
                    await conn.execute(
                        "INSERT INTO links"
                        " (id, entity_type, entity_id, url, link_type_id, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6)"
                        " ON CONFLICT DO NOTHING",
                        generate_id(), "organization", t["org_id"],
                        lnk["url"], link_type_id, lnk.get("is_canonical", False),
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers"
                        " (id, entity_id, entity_identifier_type_id, value)"
                        " VALUES ($1, $2, $3, $4)",
                        generate_id(), t["org_id"], type_id, ident["value"],
                    )
            if t["address"]:
                addr_id = generate_id()
                a = t["address"]
                await conn.execute(
                    """INSERT INTO addresses (id, raw_input, standardized, address_line_1,
                           address_line_2, city, region, postal_code, country)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    addr_id, a.get("raw_input"), a.get("standardized"),
                    a.get("address_line_1"), a.get("address_line_2"),
                    a.get("city"), a.get("region"), a.get("postal_code"),
                    a.get("country", "US"),
                )
                await conn.execute(
                    "INSERT INTO entity_addresses"
                    " (id, entity_type, entity_id, address_id, address_type)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "organization", t["org_id"], addr_id, "mailing",
                )
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(
                conn, batch_id, i, "organization", t["org_id"], "created", raw
            )

        org_index[name_lower] = t["org_id"]
        summary["orgs_loaded"] += 1

    # -------------------------------------------------------------------------
    # Pass 2: People
    # -------------------------------------------------------------------------
    for i, raw in enumerate(person_rows, start=2):
        result = validate_person(raw, source_row=i)
        if not result.ok:
            summary["people_error"] += 1
            for e in result.errors:
                logger.warning("person row %d field error: %s = %s", i, e.field, e.message)
            await _write_provenance(
                conn, batch_id, i, "person", generate_id(), "error", raw, result.errors
            )
            continue
        result = await transform_person(
            result,
            source_reliability=config.source_reliability,
        )
        for w in result.warnings:
            logger.warning("person row %d warning: %s", i, w)

        t = result.transformed
        name_lower = next(n["name"] for n in t["names"] if n["name_type"] == "legal").lower()

        existing_person = await conn.fetchrow(
            """SELECT p.id FROM people p
               JOIN person_names n ON n.person_id = p.id
               WHERE lower(n.name) = $1 AND n.name_type = 'legal' AND n.is_canonical = true""",
            name_lower,
        )
        if existing_person:
            person_index[name_lower] = existing_person["id"]
            await _write_provenance(
                conn, batch_id, i, "person", existing_person["id"], "matched", raw
            )
            summary["people_matched"] += 1
            continue

        async with conn.transaction():
            await conn.execute(
                "INSERT INTO people (id, personal_pronouns, notes) VALUES ($1, $2, $3)",
                t["person_id"], t.get("personal_pronouns"), t["notes"],
            )
            for n in t["names"]:
                await conn.execute(
                    "INSERT INTO person_names"
                    " (id, person_id, name, name_type, is_canonical)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), t["person_id"], n["name"], n["name_type"], n["is_canonical"],
                )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods"
                    " (id, entity_type, entity_id, contact_type, value)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "person", t["person_id"], cm["contact_type"], cm["value"],
                )
            for lnk in t["links"]:
                link_type_id = ref.link_type_ids.get(lnk["link_type_slug"])
                if link_type_id:
                    await conn.execute(
                        "INSERT INTO links"
                        " (id, entity_type, entity_id, url, link_type_id, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6)"
                        " ON CONFLICT DO NOTHING",
                        generate_id(), "person", t["person_id"],
                        lnk["url"], link_type_id, lnk.get("is_canonical", False),
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers"
                        " (id, entity_id, entity_identifier_type_id, value)"
                        " VALUES ($1, $2, $3, $4)",
                        generate_id(), t["person_id"], type_id, ident["value"],
                    )
            if t.get("address"):
                addr_id = generate_id()
                a = t["address"]
                await conn.execute(
                    """INSERT INTO addresses (id, raw_input, standardized, address_line_1,
                           address_line_2, city, region, postal_code, country)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    addr_id, a.get("raw_input"), a.get("standardized"),
                    a.get("address_line_1"), a.get("address_line_2"),
                    a.get("city"), a.get("region"), a.get("postal_code"),
                    a.get("country", "US"),
                )
                await conn.execute(
                    "INSERT INTO entity_addresses"
                    " (id, entity_type, entity_id, address_id, address_type)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "person", t["person_id"], addr_id, "mailing",
                )
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(
                conn, batch_id, i, "person", t["person_id"], "created", raw
            )

        person_index[name_lower] = t["person_id"]
        summary["people_loaded"] += 1

    # -------------------------------------------------------------------------
    # Pass 3: Roles + Assignments
    # Pre-populate role_index from DB so re-runs with new file hashes still
    # match existing roles rather than creating duplicates.
    # -------------------------------------------------------------------------
    for row in await conn.fetch(
        "SELECT id, organization_id, lower(title) AS title_lower"
        " FROM roles WHERE archived_at IS NULL"
    ):
        role_index[(row["organization_id"], row["title_lower"])] = row["id"]

    for i, raw in enumerate(role_rows, start=2):
        result = validate_role(raw, source_row=i)
        if not result.ok:
            summary["roles_error"] += 1
            for e in result.errors:
                logger.warning("role row %d field error: %s = %s", i, e.field, e.message)
            await _write_provenance(
                conn, batch_id, i, "role_assignment", generate_id(), "error", raw, result.errors
            )
            continue
        result = transform_role(result, org_index=org_index, person_index=person_index,
                                role_index=role_index, source_reliability=config.source_reliability)
        if not result.ok:
            summary["roles_error"] += 1
            for e in result.errors:
                logger.warning("role row %d error: %s", i, e.message)
            await _write_provenance(
                conn, batch_id, i, "role_assignment", generate_id(), "error", raw, result.errors
            )
            continue
        for w in result.warnings:
            logger.warning("role row %d warning: %s", i, w)

        t = result.transformed

        # Dedup role_assignment: NULL start_date is treated as a known value
        # (IS NOT DISTINCT FROM matches NULL = NULL).
        # CSV imports currently always produce NULL start_date (no date column in source).
        existing_ra = await conn.fetchrow(
            "SELECT id FROM role_assignments"
            " WHERE person_id = $1 AND role_id = $2 AND start_date IS NOT DISTINCT FROM $3",
            t["person_id"], t["role_id"], t.get("start_date"),
        )
        if existing_ra:
            await _write_provenance(
                conn, batch_id, i, "role_assignment", existing_ra["id"], "matched", raw
            )
            summary["roles_matched"] += 1
            continue

        async with conn.transaction():
            if t["role_action"] == "created":
                actual_role_id = await conn.fetchval(
                    "INSERT INTO roles (id, organization_id, title, notes)"
                    " VALUES ($1, $2, $3, $4)"
                    " ON CONFLICT DO NOTHING RETURNING id",
                    t["role_id"], t["org_id"], t["title"], t["notes"],
                )
                if actual_role_id is None:
                    # Conflict fired (race or pre-existing row) — look up the winner
                    actual_role_id = await conn.fetchval(
                        "SELECT id FROM roles"
                        " WHERE organization_id = $1 AND lower(title) = lower($2)"
                        " AND archived_at IS NULL",
                        t["org_id"], t["title"],
                    )
                if actual_role_id and actual_role_id != t["role_id"]:
                    t = {**t, "role_id": actual_role_id}
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
                " VALUES ($1, $2, $3, $4)",
                t["assignment_id"], t["person_id"], t["role_id"], t["is_current"],
            )
            for cm in t["contact_methods"]:
                await conn.execute(
                    "INSERT INTO contact_methods"
                    " (id, entity_type, entity_id, contact_type, value)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    generate_id(), "role_assignment",
                    t["assignment_id"], cm["contact_type"], cm["value"],
                )
            for lnk in t["links"]:
                link_type_id = ref.link_type_ids.get(lnk["link_type_slug"])
                if link_type_id:
                    await conn.execute(
                        "INSERT INTO links"
                        " (id, entity_type, entity_id, url, link_type_id, is_canonical)"
                        " VALUES ($1, $2, $3, $4, $5, $6)"
                        " ON CONFLICT DO NOTHING",
                        generate_id(), "role_assignment", t["assignment_id"],
                        lnk["url"], link_type_id, lnk.get("is_canonical", False),
                    )
            for ident in t["identifiers"]:
                type_id = ref.identifier_type_ids.get(ident["identifier_type_slug"])
                if type_id:
                    await conn.execute(
                        "INSERT INTO identifiers"
                        " (id, entity_id, entity_identifier_type_id, value)"
                        " VALUES ($1, $2, $3, $4)",
                        generate_id(), t["assignment_id"], type_id, ident["value"],
                    )
            for rec in t["confidence_records"]:
                rec.assessed_by = f"import:{batch_id}"
            await _write_confidence(conn, t["confidence_records"])
            await _write_provenance(
                conn, batch_id, i, "role_assignment", t["assignment_id"], "created", raw
            )

        role_key = (t["org_id"], t["title"].lower())
        role_index[role_key] = t["role_id"]
        summary["roles_loaded"] += 1

    # -------------------------------------------------------------------------
    # Update import_batches with final counts
    # -------------------------------------------------------------------------
    loaded = summary["orgs_loaded"] + summary["people_loaded"] + summary["roles_loaded"]
    matched = summary["orgs_matched"] + summary["people_matched"] + summary["roles_matched"]
    errors = summary["orgs_error"] + summary["people_error"] + summary["roles_error"]

    # Only update counts on first run (loaded_count = 0 means fresh batch).
    await conn.execute(
        """UPDATE import_batches
           SET loaded_count = $1, error_count = $2
           WHERE id = $3 AND loaded_count = 0""",
        loaded + matched,
        errors,
        batch_id,
    )

    elapsed = time.monotonic() - start
    logger.info(
        "import complete: batch=%s loaded=%d matched=%d errors=%d elapsed=%.1fs",
        batch_id, loaded, matched, errors, elapsed,
    )
    summary["elapsed_s"] = elapsed
    return summary
