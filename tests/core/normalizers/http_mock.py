"""Shared httpx mock helper for normalizer unit tests."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch


@contextmanager
def mock_http_client(response=None, *, method="post", side_effect=None):
    """Patch httpx.AsyncClient; yield the mock class for call inspection.

    Pass ``response`` for a successful (or error-status) reply, or
    ``side_effect`` to raise an exception from the request method.
    """
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        m = (
            AsyncMock(side_effect=side_effect)
            if side_effect is not None
            else AsyncMock(return_value=response)
        )
        setattr(MockClient.return_value, method, m)
        yield MockClient
