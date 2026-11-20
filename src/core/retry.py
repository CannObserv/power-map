"""Bounded in-process retry of a timer's network call (issue #833).

On 2026-11-17 the VM lost outbound network for ten minutes, and every timer that
fired in that window failed on its first error. A daily unit then lost a day:
of dataset currency, of the ``--execute`` streak, of a co-status monitor.

So a daily or hourly unit that reads the network retries **the network call
only**, the connect or the GET that wrote nothing, and reports one verdict.
systemd's ``Restart=`` was rejected: its check-in hook runs after every attempt
(``alert`` then ``ok``), it retries a real finding as readily as a blip, and it
re-runs the whole unit. The policy, per unit, is in ``docs/ALERTING.md``.

- **One budget per run.** A ``Backoff`` holds ``DELAYS``, and every retried call
  in the run draws from it; a success never refills it. So however many calls a
  run makes, it waits at most ``sum(DELAYS)``, which is what lets a unit's
  worst case be computed and held inside its ``TimeoutStartSec``
  (``tests/test_infra_retry.py``).
- **Transience is judged per library.** httpx and httpx2 share no exception
  classes (#831), and asyncpg raises its own. Each call site passes the
  classifier for the library it calls. A TLS failure is permanent wherever it
  appears in the exception chain: ``ssl.SSLError`` is an ``OSError``, and
  httpx wraps one in ``ConnectError``.
- **Every** ``socket.gaierror`` **is transient**, ``EAI_NONAME`` included. A
  resolver with no upstream promises no particular code; a mistyped host costs
  an alert eleven minutes late, a misread outage costs a day.
"""

import asyncio
import ssl
import time
from collections.abc import Awaitable, Callable, Iterable

import asyncpg
import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)

# 645 s of waiting: rides out the 10-minute outage of 2026-11-17 however fast
# each attempt fails.
DELAYS: tuple[float, ...] = (15.0, 30.0, 60.0, 120.0, 180.0, 240.0)
# Per connect attempt. asyncpg's default is 60 s; the DB's RTT is ~10 ms.
CONNECT_TIMEOUT = 15.0

_TRANSIENT_PG = (
    asyncpg.CannotConnectNowError,  # 57P03: starting up, shutting down
    asyncpg.TooManyConnectionsError,  # 53300
    asyncpg.ConnectionDoesNotExistError,  # dropped mid-handshake
)
_TRANSIENT_HTTPX = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class Backoff:
    """One run's retry budget: the delays still unspent, shared by every call."""

    def __init__(self, delays: Iterable[float] = DELAYS) -> None:
        self._delays = list(delays)

    def take(self) -> float | None:
        """The next delay, or None once the budget is spent."""
        return self._delays.pop(0) if self._delays else None

    async def wait(self, seconds: float) -> None:
        """Sleep *seconds* (looked up at call time, so tests can record it)."""
        await _sleep(seconds)


def _ssl_in_chain(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def is_transient_connect(exc: BaseException) -> bool:
    """For ``asyncpg.connect``: could asking again plausibly get a different answer?"""
    if _ssl_in_chain(exc):
        return False
    return isinstance(exc, (OSError, *_TRANSIENT_PG))


def is_transient_httpx(exc: BaseException) -> bool:
    """For a call through plain ``httpx`` (not httpx2, #831)."""
    if _ssl_in_chain(exc):
        return False
    return isinstance(exc, _TRANSIENT_HTTPX)


async def retry_async[T](
    call: Callable[[], Awaitable[T]],
    *,
    transient: Callable[[BaseException], bool],
    backoff: Backoff,
    label: str,
) -> T:
    """Await *call*, retrying a *transient* failure while *backoff* has budget.

    A permanent failure is raised at once, the last transient one once the
    budget is spent. A call that needed a retry logs one WARNING when it
    succeeds, so a degraded night shows in ``journalctl -p warning``.
    """
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await call()
        except Exception as exc:
            if not transient(exc):
                raise
            delay = backoff.take()
            if delay is None:
                logger.warning(
                    "%s: attempt %d failed (%s); retry budget spent after %d attempts",
                    label,
                    attempt,
                    exc.__class__.__name__,
                    attempt,
                )
                raise
            logger.info(
                "%s: attempt %d failed (%s); retrying in %gs",
                label,
                attempt,
                exc.__class__.__name__,
                delay,
            )
            await backoff.wait(delay)
            continue
        if attempt > 1:
            logger.warning(
                "%s: succeeded after %d attempts (%.0fs)",
                label,
                attempt,
                time.monotonic() - started,
            )
        return result


async def connect_with_retry(
    dsn: str,
    *,
    backoff: Backoff,
    timeout: float = CONNECT_TIMEOUT,
    label: str = "database connect",
    **kwargs,
) -> asyncpg.Connection:
    """``asyncpg.connect`` with a bounded per-attempt timeout, retried while transient.

    Only the connect is retried: nothing has been written yet. A query on an
    open connection is never retried here.
    """
    return await retry_async(
        lambda: asyncpg.connect(dsn, timeout=timeout, **kwargs),
        transient=is_transient_connect,
        backoff=backoff,
        label=label,
    )
