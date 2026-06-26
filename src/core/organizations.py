"""Core organization write helpers shared across admin and public surfaces."""


class ActiveOnArchivedOrg(Exception):
    """Raised when an org's ``active`` flag is set on an archived row.

    Archiving is an admin lifecycle gate; the ``active`` axis is orthogonal but
    an archived org is not a valid target for an active assertion. Both the
    admin toggle and the public observation pipeline reject the operation —
    each maps this to its own surface-appropriate response.
    """


class OrgNotFound(Exception):
    """Raised when the target organization row does not exist.

    The ``FOR UPDATE`` row read returns no row — e.g. the org was hard-deleted
    concurrently between a caller's existence check and this write. Callers map
    it to their own response (admin → 404); the public path cannot trigger it
    because ``resolve_entity`` guarantees the org exists within the same txn.
    """


async def set_org_active(conn, organization_id: str, active: bool) -> None:
    """Set an organization's ``active`` flag with archived + no-op guards.

    The row is locked ``FOR UPDATE`` so the archived check and the write are
    atomic against a concurrent admin archive — the caller MUST run this inside
    an open transaction for the lock to be held until commit. The flag is
    written only when it actually changes; a redundant assertion is a true
    no-op that does not fire ``fn_record_entity_change`` (no spurious 'updated'
    event) and does not bump ``updated_at``.

    Raises OrgNotFound if the row is absent, ActiveOnArchivedOrg if it is
    archived.
    """
    row = await conn.fetchrow(
        "SELECT archived_at, active FROM organizations WHERE id=$1 FOR UPDATE",
        organization_id,
    )
    if row is None:
        raise OrgNotFound
    if row["archived_at"] is not None:
        raise ActiveOnArchivedOrg
    if row["active"] != active:
        await conn.execute(
            "UPDATE organizations SET active=$1 WHERE id=$2", active, organization_id
        )
