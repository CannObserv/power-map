"""Public API authentication dependency."""

import hashlib
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import APIKeyHeader

from src.api.deps import get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass
class AuthedKey:
    """Authenticated API key context — returned by require_scope."""

    user_id: str
    key_id: str


async def _resolve_api_key(raw_key: str | None, db, request: Request | None = None) -> dict:
    """Validate raw key, update last_used_at, return the api_keys row.

    Raises 403 when raw_key is None, 401 when key hash is not found. On success,
    stashes ``request.state.api_key_id`` so the capture middleware (#260) can
    record request identity without re-hashing the key.
    """
    if raw_key is None:
        raise HTTPException(status_code=403, detail="Not authenticated")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.fetchrow("SELECT id, user_id FROM api_keys WHERE key_hash = $1", key_hash)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    await db.execute("UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", row["id"])
    if request is not None:
        request.state.api_key_id = row["id"]
    return row


async def require_api_key(
    raw_key: str | None = Depends(api_key_header),
    db=Depends(get_db),
    request: Request = None,
) -> str:
    """Validate X-API-Key header; return user_id on success.

    Raises 403 when header is absent, 401 when key is invalid.
    Also updates last_used_at on the matching api_keys row.
    """
    row = await _resolve_api_key(raw_key, db, request)
    return row["user_id"]


async def require_key(
    raw_key: str | None = Depends(api_key_header),
    db=Depends(get_db),
    request: Request = None,
) -> AuthedKey:
    """Validate X-API-Key; return AuthedKey (user_id + key_id) without scope check."""
    row = await _resolve_api_key(raw_key, db, request)
    return AuthedKey(user_id=row["user_id"], key_id=row["id"])


def require_scope(scope_id: str):
    """Return a FastAPI dependency that requires the given scope on the API key.

    Usage: auth: AuthedKey = Depends(require_scope("observations:write"))
    Raises 403 (not authenticated), 401 (invalid key), or 403 (insufficient scope).
    Returns AuthedKey with user_id and key_id.
    """

    async def _check(
        raw_key: str | None = Depends(api_key_header),
        db=Depends(get_db),
        request: Request = None,
    ) -> AuthedKey:
        row = await _resolve_api_key(raw_key, db, request)
        scope_row = await db.fetchrow(
            "SELECT 1 FROM api_key_scopes WHERE api_key_id = $1 AND scope_id = $2",
            row["id"],
            scope_id,
        )
        if not scope_row:
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return AuthedKey(user_id=row["user_id"], key_id=row["id"])

    return _check


def identifier_filter(
    identifier_type: str | None = Query(default=None),
    identifier_value: str | None = Query(default=None),
) -> tuple[str | None, str | None]:
    """Validate that identifier_type and identifier_value are supplied together.

    Both must be present and non-empty, or both must be absent; one alone or either
    empty raises 422.
    """
    both_present = identifier_type is not None and identifier_value is not None
    neither_present = identifier_type is None and identifier_value is None
    if not both_present and not neither_present:
        raise HTTPException(
            status_code=422,
            detail="identifier_type and identifier_value must be supplied together",
        )
    if both_present and (not identifier_type.strip() or not identifier_value.strip()):
        raise HTTPException(
            status_code=422,
            detail="identifier_type and identifier_value must not be empty",
        )
    return identifier_type, identifier_value
