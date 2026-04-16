"""Admin settings: API key management CRUD views."""

import hashlib
import os

from fastapi import APIRouter

router = APIRouter(prefix="/settings/api-keys", tags=["admin-settings-api-keys"])


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_prefix).

    raw_key:    "pm_" + 32 hex chars (128-bit random via os.urandom)
    key_hash:   SHA-256 hex of raw_key — stored in DB; never returned after creation
    key_prefix: first 8 chars of raw_key — stored for display identification
    """
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix
