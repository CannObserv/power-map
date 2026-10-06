"""``power_map_client.connect`` authenticates the way the API does (#618).

The spec declares an ``X-API-Key`` header, but the generator's
``AuthenticatedClient`` defaults to ``Authorization: Bearer <token>``. Every
consumer had to rediscover ``prefix=""`` and ``auth_header_name="X-API-Key"``;
``connect`` sets both.
"""

from importlib.metadata import version

import httpx
import power_map_client
from power_map_client import AuthenticatedClient, connect
from power_map_client.generated.api.health import get_health


def _capture():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "build": "0.0.0"})

    return seen, httpx.MockTransport(handler)


def test_connect_sends_the_key_as_x_api_key():
    seen, transport = _capture()
    client = connect("http://pm.test", "pm_secret", httpx_args={"transport": transport})
    get_health.sync_detailed(client=client)
    assert seen[0].headers["X-API-Key"] == "pm_secret"
    assert "Authorization" not in seen[0].headers


def test_connect_returns_an_authenticated_client():
    client = connect("http://pm.test", "pm_secret")
    assert isinstance(client, AuthenticatedClient)
    assert client.raise_on_unexpected_status is False


def test_connect_passes_options_through():
    client = connect("http://pm.test", "pm_secret", timeout=httpx.Timeout(3.0))
    assert client._timeout == httpx.Timeout(3.0)


def test_version_is_the_distribution_version():
    assert power_map_client.__version__ == version("power-map-client")
