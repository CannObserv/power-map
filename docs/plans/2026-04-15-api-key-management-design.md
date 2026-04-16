# API Key Management Design

**Date:** 2026-04-15
**Status:** Approved

## Goal

Enable programmatic (non-browser) R/W access to power-map from outside the VM via static API keys managed through the admin UI.

## Approved Approach

Option A: make the exe.dev proxy public + application-layer `X-API-Key` auth on a new `/api/v1/` router.

- `ssh exe.dev share set-public power-map` exposes port 8000 to the internet.
- Admin routes keep their exe.dev header guard; unauthenticated browser requests still redirect to login.
- A new `src/api/public/` package handles API routes with `X-API-Key` header auth.
- API keys are managed in the admin UI under Settings → API Keys.

## Data Model

### `app_users`

Thin identity anchor keyed by exe.dev user ID. One row per user, created (or email-updated) lazily on every admin login via upsert in `get_admin_user`.

```sql
CREATE TABLE IF NOT EXISTS app_users (
    id         TEXT        PRIMARY KEY,  -- X-ExeDev-UserID value
    email      TEXT        NOT NULL,     -- X-ExeDev-Email, updated on each login
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `api_keys`

```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT        PRIMARY KEY,  -- ULID
    user_id      TEXT        NOT NULL REFERENCES app_users(id),
    label        TEXT        NOT NULL,
    key_prefix   TEXT        NOT NULL,     -- first 8 chars of raw key, for display
    key_hash     TEXT        NOT NULL UNIQUE,  -- SHA-256 hex of raw key
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);
```

No `archived_at` — direct hard delete. API keys are credentials, not data records; the archive-before-delete convention adds friction without benefit.

## Key Mechanics

- **Format:** `pm_` + 32 random hex chars (128 bits via `os.urandom`). Example: `pm_a3f8c2d1...`
- **Storage:** SHA-256 hex hash of the full raw key. Random keys have no dictionary attack surface; SHA-256 is the industry standard (GitHub PATs, Stripe).
- **Prefix:** first 8 chars of the raw key stored in `key_prefix` for display (e.g. `pm_a3f8c2`), so keys are identifiable in the list without exposing the secret.
- **Show once:** full raw key returned in the create response only, displayed in a one-time modal with a copy button. Never retrievable after dismissal.

## User Provisioning

`get_admin_user` (in `deps.py`) upserts into `app_users` after validating exe.dev headers on every admin request:

```python
await db.execute(
    """
    INSERT INTO app_users (id, email) VALUES ($1, $2)
    ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
    """,
    user_id, email,
)
```

No separate onboarding step; the row appears silently on first login after the migration.

## Admin UI

New card on the Settings landing page: **API Keys** (count of active keys).

Routes under `/admin/settings/api-keys/`:

| Route | Action |
|---|---|
| `GET /` | List keys: label, prefix, created_at, last_used_at; "Generate new key" button |
| `GET /new-row/` | Inline blank form row (label input + Save/Cancel) |
| `POST /` | Create key → 200 with one-time modal partial containing full raw key |
| `GET /{id}/edit-row/` | Inline label edit form |
| `POST /{id}/edit-row/` | Save updated label → read row partial |
| `GET /{id}/read-row/` | Read row partial (Cancel on edit) |
| `DELETE /{id}/` | Hard delete — no archive step |

The one-time key display uses existing `admin-modal.js` infrastructure. Modal includes:
- Full raw key in a monospace input (pre-selected for copy)
- Copy-to-clipboard button
- Warning: "This key will not be shown again. Copy it now."

## Public API Router

New `src/api/public/` package, mounted at `/api/v1/` in `main.py`.

Auth dependency (`src/api/public/deps.py`):

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
import hashlib

api_key_header = APIKeyHeader(name="X-API-Key")

async def require_api_key(
    raw_key: str = Security(api_key_header),
    db=Depends(get_db),
) -> str:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.fetchrow(
        "SELECT ak.id, ak.user_id FROM api_keys ak WHERE key_hash = $1", key_hash
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    await db.execute(
        "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", row["id"]
    )
    return row["user_id"]
```

No actual API endpoints are in scope for this issue — only the auth infrastructure and router scaffold.

## exe.dev Change

```bash
ssh exe.dev share set-public power-map
```

Makes port 8000 publicly accessible. Admin routes retain their exe.dev header guard; unauthenticated browser users are redirected to `/__exe.dev/login`.

## Key Decisions

- **`X-API-Key` over `Authorization: Bearer`** — same security, simpler ergonomics for scripting; already proven on exe.dev VMs.
- **SHA-256 over bcrypt** — appropriate for random high-entropy keys; no dictionary attack surface; faster auth on every API request.
- **Hard delete (no archive)** — API keys are ephemeral credentials; the show-once model already treats them as disposable.
- **Lazy user provisioning** — no onboarding UI needed; row appears on first login.
- **Single public port** — exe.dev only supports one public port; admin and API share port 8000.

## Out of Scope

- Key expiry or automatic rotation
- Key scopes / permissions (all keys have full R/W access)
- Rate limiting
- Multi-user support
- Actual `/api/v1/` endpoint implementations
