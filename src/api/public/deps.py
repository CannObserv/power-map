"""Public API authentication dependency."""

import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from src.api.deps import get_db

# auto_error=False so we can distinguish missing header (403) from invalid key (401)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    raw_key: str | None = Depends(api_key_header),
    db=Depends(get_db),
) -> str:
    """Validate X-API-Key header; return user_id on success.

    Raises 403 when header is absent, 401 when key is invalid.
    Also updates last_used_at on the matching api_keys row.
    """
    if raw_key is None:
        raise HTTPException(status_code=403, detail="Not authenticated")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.fetchrow(
        "SELECT id, user_id FROM api_keys WHERE key_hash = $1", key_hash
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    await db.execute(
        "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", row["id"]
    )
    return row["user_id"]
