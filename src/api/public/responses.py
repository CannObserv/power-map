"""OpenAPI error declarations shared by the public routes (#618).

A generated client types each declared response and returns any other status
untyped, so a route declares every status it can answer with. The shared auth
dependency raises 401 (unknown key), 403 (no key, or a missing scope) and 429
(throttled) on every route; the public router declares those once with
:data:`AUTH_RESPONSES`. A route that raises 404 or 409 itself spreads
:data:`NOT_FOUND` or :data:`CONFLICT` into its decorator.
``tests/api/public/test_openapi_responses.py`` sweeps that each raised status is
declared.

Immutable, like ``NOT_MODIFIED``: the same mapping is shared by many routes.
"""

from types import MappingProxyType
from typing import Final

from src.api.public.etag import NOT_MODIFIED
from src.api.public.schemas import ErrorDetail

AUTH_RESPONSES: Final = MappingProxyType(
    {
        401: {"model": ErrorDetail, "description": "Invalid API key."},
        403: {
            "model": ErrorDetail,
            "description": "No X-API-Key header, or the key lacks the route's scope.",
        },
        429: {
            "model": ErrorDetail,
            "description": "Rate limit exceeded — back off for Retry-After seconds. "
            "See PUBLIC_API.md § Rate Limits.",
        },
    }
)

NOT_FOUND: Final = MappingProxyType(
    {404: {"model": ErrorDetail, "description": "Not found — `detail` says what was missing."}}
)

CONFLICT: Final = MappingProxyType(
    {409: {"model": ErrorDetail, "description": "Conflicts with the row's current state."}}
)

# A conditional sub-resource or list under an entity id: its 304, plus the 404
# for an unknown parent. Counted by the 304 sweep alongside NOT_MODIFIED.
NOT_MODIFIED_OR_NOT_FOUND: Final = MappingProxyType({**NOT_MODIFIED, **NOT_FOUND})
