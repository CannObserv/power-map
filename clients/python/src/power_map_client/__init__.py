"""Python client for the power-map public API (#618).

``generated/`` is ``openapi-python-client`` output from ``clients/python/openapi.json``
and is never edited by hand. This module adds one thing to it: :func:`connect`,
an :class:`AuthenticatedClient` that sends the key the way the API expects it.
Retries, error taxonomy and model bridging belong in each consumer's adapter.
"""

from importlib.metadata import version
from typing import Any

from power_map_client.generated.client import AuthenticatedClient, Client

__all__ = ["AuthenticatedClient", "Client", "__version__", "connect"]

__version__ = version("power-map-client")


def connect(base_url: str, api_key: str, **kwargs: Any) -> AuthenticatedClient:
    """Return a client that sends ``api_key`` as the ``X-API-Key`` header.

    The generated ``AuthenticatedClient`` defaults to ``Authorization: Bearer``;
    the API's scheme is ``APIKeyHeader`` (``X-API-Key``, no prefix). Any other
    ``AuthenticatedClient`` option (``timeout``, ``httpx_args``,
    ``raise_on_unexpected_status``, …) passes through.
    """
    return AuthenticatedClient(
        base_url=base_url,
        token=api_key,
        prefix="",
        auth_header_name="X-API-Key",
        **kwargs,
    )
