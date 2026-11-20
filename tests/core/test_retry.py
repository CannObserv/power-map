"""Tests for src.core.retry — a timer's bounded transient retry (issue #833).

The real clients are driven against failures made locally — a refused loopback
port, an ``.invalid`` host — as #831's note recommends, and the assertions are
on what the caller sees: the result or the error, the attempt count and the
delays slept. Sleeping is recorded, never waited.
"""

import os
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest

from src.core import retry
from src.core.retry import (
    CONNECT_TIMEOUT,
    DELAYS,
    Backoff,
    connect_with_retry,
    is_transient_connect,
    is_transient_httpx,
    retry_async,
)


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Every delay a Backoff would have slept, in order; nothing actually sleeps."""
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(retry, "_sleep", record)
    return delays


def refused_port() -> int:
    """A loopback port nothing listens on: bound, read, closed."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def flaky(failures: list[BaseException], result: object = "ok"):
    """An async call that raises each of *failures* in turn, then returns *result*."""
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return result

    call.calls = calls
    return call


# --- the budget -------------------------------------------------------------


def test_the_budget_rides_out_a_ten_minute_outage():
    """The issue's floor: 11-17's outage lasted 10 minutes (08:58–09:08Z)."""
    assert sum(DELAYS) >= 600


def test_delays_never_shrink():
    assert list(DELAYS) == sorted(DELAYS)


async def test_a_transient_failure_is_retried_until_it_succeeds(slept):
    call = flaky([ConnectionRefusedError(), TimeoutError()], result=7)
    assert (
        await retry_async(call, transient=is_transient_connect, backoff=Backoff(), label="t") == 7
    )
    assert call.calls["n"] == 3
    assert slept == list(DELAYS[:2])


async def test_a_permanent_failure_raises_at_once_without_sleeping(slept):
    call = flaky([ValueError("bad body")])
    with pytest.raises(ValueError):
        await retry_async(call, transient=is_transient_connect, backoff=Backoff(), label="t")
    assert call.calls["n"] == 1
    assert slept == []


async def test_a_spent_budget_reraises_the_last_error(slept):
    call = flaky([ConnectionRefusedError() for _ in range(len(DELAYS) + 1)])
    with pytest.raises(ConnectionRefusedError):
        await retry_async(call, transient=is_transient_connect, backoff=Backoff(), label="t")
    assert call.calls["n"] == len(DELAYS) + 1
    assert slept == list(DELAYS)


async def test_the_budget_is_one_per_run_and_never_refills(slept):
    """A second call in the same run gets only what the first left."""
    backoff = Backoff()
    first = flaky([OSError(), OSError(), OSError()])
    await retry_async(first, transient=is_transient_connect, backoff=backoff, label="a")
    second = flaky([OSError() for _ in range(10)])
    with pytest.raises(OSError):
        await retry_async(second, transient=is_transient_connect, backoff=backoff, label="b")
    assert second.calls["n"] == len(DELAYS) - 3 + 1
    assert slept == list(DELAYS)


async def test_a_recovered_call_logs_one_warning(slept, caplog):
    """A degraded night must show in `journalctl -p warning` even though it ended ok."""
    call = flaky([OSError()])
    with caplog.at_level("INFO", logger="src.core.retry"):
        await retry_async(call, transient=is_transient_connect, backoff=Backoff(), label="catalog")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "catalog" in warnings[0].getMessage()
    assert "2 attempts" in warnings[0].getMessage()


async def test_a_first_time_success_logs_nothing_above_debug(slept, caplog):
    with caplog.at_level("DEBUG", logger="src.core.retry"):
        await retry_async(flaky([]), transient=is_transient_connect, backoff=Backoff(), label="t")
    assert not [r for r in caplog.records if r.levelno >= 20]


async def test_giving_up_logs_a_warning_naming_the_attempts(slept, caplog):
    call = flaky([OSError() for _ in range(len(DELAYS) + 1)])
    with caplog.at_level("INFO", logger="src.core.retry"), pytest.raises(OSError):
        await retry_async(call, transient=is_transient_connect, backoff=Backoff(), label="db")
    last = [r for r in caplog.records if r.levelname == "WARNING"][-1].getMessage()
    assert "db" in last and f"{len(DELAYS) + 1} attempts" in last


# --- classifiers ------------------------------------------------------------


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize(
    "exc",
    [
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
        # A resolver with no upstream promises nothing; a typo alerts late, not never.
        socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
        ConnectionRefusedError(),
        ConnectionResetError(),
        TimeoutError(),
        OSError("Multiple exceptions: [Errno 111] Connect call failed"),
        asyncpg.CannotConnectNowError("the database system is starting up"),
        asyncpg.TooManyConnectionsError("too many connections"),
        asyncpg.ConnectionDoesNotExistError("connection was closed"),
    ],
    ids=repr,
)
def test_transient_connect_errors(exc):
    assert is_transient_connect(exc)


@pytest.mark.parametrize(
    "exc",
    [
        # SSLError *is* an OSError: a verify-full failure (#667) won't heal in 10 min.
        ssl.SSLCertVerificationError("certificate verify failed"),
        ssl.SSLError("wrong version number"),
        _chained(OSError("handshake"), ssl.SSLCertVerificationError("verify failed")),
        asyncpg.InvalidPasswordError("password authentication failed"),
        asyncpg.InvalidAuthorizationSpecificationError("no pg_hba.conf entry"),
        asyncpg.InvalidCatalogNameError('database "x" does not exist'),
        ValueError("invalid DSN"),
    ],
    ids=repr,
)
def test_permanent_connect_errors(exc):
    assert not is_transient_connect(exc)


_REQ = httpx.Request("GET", "https://example.invalid/")


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("[Errno -3] Temporary failure in name resolution", request=_REQ),
        httpx.ConnectTimeout("timed out", request=_REQ),
        httpx.ReadTimeout("timed out", request=_REQ),
        httpx.PoolTimeout("timed out", request=_REQ),
        httpx.ReadError("reset", request=_REQ),
        httpx.RemoteProtocolError("server disconnected", request=_REQ),
    ],
    ids=repr,
)
def test_transient_httpx_errors(exc):
    assert is_transient_httpx(exc)


@pytest.mark.parametrize(
    "exc",
    [
        # httpx wraps a TLS verify failure in ConnectError: the cause decides.
        _chained(
            httpx.ConnectError("certificate verify failed", request=_REQ),
            ssl.SSLCertVerificationError("certificate verify failed"),
        ),
        httpx.UnsupportedProtocol("no scheme", request=_REQ),
        httpx.LocalProtocolError("bad header", request=_REQ),
        ConnectionRefusedError(),  # not raised through httpx: not its call site
        ValueError("x"),
    ],
    ids=repr,
)
def test_permanent_httpx_errors(exc):
    assert not is_transient_httpx(exc)


# --- connect_with_retry, real asyncpg ---------------------------------------


@pytest.fixture
def connects(monkeypatch) -> list[dict]:
    """Every asyncpg.connect call's kwargs; the real client still runs."""
    calls: list[dict] = []
    real = asyncpg.connect

    async def spy(*args, **kwargs):
        calls.append(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncpg, "connect", spy)
    return calls


async def test_a_refused_port_is_retried_through_the_whole_budget(slept, connects):
    dsn = f"postgresql://u:p@127.0.0.1:{refused_port()}/db"
    with pytest.raises(OSError):
        await connect_with_retry(dsn, backoff=Backoff())
    assert len(connects) == len(DELAYS) + 1
    assert slept == list(DELAYS)


async def test_an_unresolvable_host_is_retried(slept, connects):
    with pytest.raises(OSError):
        await connect_with_retry("postgresql://u:p@pm-833.invalid:5432/db", backoff=Backoff())
    assert len(connects) == len(DELAYS) + 1


async def test_each_attempt_is_bounded_by_the_connect_timeout(slept, connects):
    """asyncpg's own default is 60 s: a black-holed connect would eat the budget 4× over."""
    with pytest.raises(OSError):
        await connect_with_retry(
            f"postgresql://u:p@127.0.0.1:{refused_port()}/db", backoff=Backoff()
        )
    assert {c["timeout"] for c in connects} == {CONNECT_TIMEOUT}


async def test_a_permanent_connect_error_is_not_retried(slept, monkeypatch):
    calls = {"n": 0}

    async def refuse(*args, **kwargs):
        calls["n"] += 1
        raise asyncpg.InvalidPasswordError("password authentication failed")

    monkeypatch.setattr(asyncpg, "connect", refuse)
    with pytest.raises(asyncpg.InvalidPasswordError):
        await connect_with_retry("postgresql://u:p@db/x", backoff=Backoff())
    assert calls["n"] == 1
    assert slept == []


async def test_a_recovered_connect_returns_the_connection(slept, monkeypatch):
    sentinel = object()
    failures = [socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")]

    async def connect(*args, **kwargs):
        if failures:
            raise failures.pop()
        return sentinel

    monkeypatch.setattr(asyncpg, "connect", connect)
    assert await connect_with_retry("postgresql://u:p@db/x", backoff=Backoff()) is sentinel
    assert slept == [DELAYS[0]]


@pytest.mark.integration
async def test_a_wrong_password_is_refused_once_by_the_real_server(slept):
    """The permanent half against a real server: auth fails in the run, unretried.

    Only the password changes, so the host, port, TLS and CONNECT grant all
    match the test DB's: the server's answer can only be about the password.
    """
    parts = urlsplit(os.environ["TEST_DATABASE_URL"])
    netloc = f"{parts.username}:not-the-password-833@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    dsn = urlunsplit(parts._replace(netloc=netloc))
    with pytest.raises(asyncpg.InvalidPasswordError):
        await connect_with_retry(dsn, backoff=Backoff())
    assert slept == []
