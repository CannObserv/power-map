"""Route enumeration guards (#772).

FastAPI 0.137 stopped flattening included routers into ``app.routes``. Code
that read ``app.routes`` for ``APIRoute``s then saw none, and nothing failed.
The rate limiter's read-POST set went quietly empty. These pins are absolute
numbers, so an enumeration that comes back empty or partial fails here and
can't pass vacuously.
"""

from fastapi.routing import APIRoute

from src.api.main import app
from src.api.public import ratelimit

# Every APIRoute the app serves, counting admin, public and probes. A route
# added or removed changes this number; update it in the same diff. Routes under
# ``/_test/`` are left out because test modules register them on ``app`` at
# import (``public/test_require_scope.py``), so counting them would make the
# total depend on which modules ran first.
API_ROUTE_COUNT = 406

# The POST routes marked ``x-rate-limit-bucket: read`` (#310).
READ_POST_PATHS = frozenset(
    {
        "/api/v1/orgs/batch-get",
        "/api/v1/people/embeddings/presence",
        "/api/v1/people/identify",
        "/api/v1/people/identify-batch",
        "/api/v1/people/verify",
        "/api/v1/people/verify-batch",
    }
)


def test_installed_read_post_set_is_the_six_marked_paths():
    """Checks the set ``main`` installed at import, not a re-derivation."""
    assert ratelimit._post_read_paths == READ_POST_PATHS


def test_route_walk_yields_every_api_route():
    served = [r for r in app.routes if isinstance(r, APIRoute) and "/_test/" not in r.path]
    assert len(served) == API_ROUTE_COUNT
