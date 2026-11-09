"""The app's one route enumeration (#772).

Since FastAPI 0.137 ``app.routes`` is a tree: ``include_router`` adds a wrapper,
not the router's ``APIRoute``s. A filter on ``isinstance(r, APIRoute)`` over it
finds none and fails silently. ``iter_route_contexts`` walks the tree, and its
API is still marked alpha, so every reader goes through this module.
``tests/api/test_routing.py`` sweeps for any other reader.
"""

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts


def iter_api_routes(app: FastAPI) -> Iterator[RouteContext]:
    """Yield every ``APIRoute`` the app serves, as its effective route.

    Yields the ``RouteContext``, not ``ctx.original_route``: the context forwards
    attribute reads to the route as included. That gives the full prefixed
    ``path`` and a ``dependant`` with app- and router-level dependencies merged
    in, plus ``methods``, ``openapi_extra``, ``param_convertors``,
    ``include_in_schema`` and ``operation_id``. The router's own ``APIRoute``
    has neither the prefix nor the inherited dependencies.
    """
    for ctx in iter_route_contexts(app.routes):
        if isinstance(ctx.original_route, APIRoute):
            yield ctx
