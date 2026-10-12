"""The change feed is retired (#502): no route, model or field of it is served.

usa-wa was the feed's only consumer, and the #490 dataset subscription replaced
it. The detail GETs' ``410`` + ``merged_into`` (#607) carry the merge signal
now. Pure unit: walks the app's routes and its published OpenAPI schema.
"""

import json

from fastapi.routing import APIRoute

from src.api.main import app

_RETIRED_PREFIXES = ("/api/v1/changes", "/api/v1/subscriptions")
_RETIRED_MODELS = {
    "ChangeItem",
    "ChangeMeta",
    "ChangeFeedResponse",
    "DiscoveryItem",
    "DiscoveryMeta",
    "DiscoveryResponse",
}


def test_no_feed_route_is_served():
    served = sorted(
        r.path
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith(_RETIRED_PREFIXES)
    )
    assert not served, f"retired feed routes still served: {served}"


def test_schema_publishes_no_feed_model():
    models = set(app.openapi().get("components", {}).get("schemas", {}))
    assert not models & _RETIRED_MODELS, sorted(models & _RETIRED_MODELS)
    assert not [m for m in models if "Subscription" in m], sorted(models)


def test_schema_carries_no_min_seq():
    assert "min_seq" not in json.dumps(app.openapi())
