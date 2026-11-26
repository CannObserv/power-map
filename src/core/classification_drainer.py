"""The in-process classification drainer and its wake hook (#877 phase 2).

Each API worker runs one :class:`Drainer` (started in the app lifespan). It
waits for :func:`wake` or a tick, then runs one
:func:`src.core.classification.drain` on its own pool connection: rules, then
Jev for at most ``IN_PROCESS_LIMIT`` orgs. The drain's advisory lock keeps the
workers (and the CLI and timer) from classifying at once. The backlog itself
is the supervised CLI's job, so a worker never holds a connection for long.

Acquiring from the pool here is the documented exception to "routes use
``Depends(get_db)`` only" (``docs/SCHEMA.md``): this is not a route handler.

:func:`wake` is called by every in-process path that creates an organization
(``tests/core/test_classification_drainer.py`` sweeps ``src/`` for them). It
sets an event and returns: it never touches Jev or the database, never blocks
and never fails the write. A short debounce lets the creating transaction
commit first; an org it misses is picked up by the next tick. Out-of-process
creators (the applier, scripts) rely on the tick and the daily timer.

A failed drain logs a WARNING and the loop goes on; the health card and the
daily timer's check-in carry failures to a person. It never checks in itself.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import asyncpg

from src.core import classification as cls
from src.core.logging import get_logger

logger = get_logger(__name__)

TICK_SECONDS = 15 * 60
DEBOUNCE_SECONDS = 5.0
# Orgs asked per in-process drain: a handful are created a day.
IN_PROCESS_LIMIT = 50

DrainFn = Callable[..., Awaitable[cls.DrainResult]]

_current: "Drainer | None" = None


def wake() -> None:
    """Ask the running drainer, if any, to drain soon. Never raises."""
    drainer = _current
    if drainer is not None:
        drainer.event.set()


class Drainer:
    """One worker's drain loop: on :func:`wake` (debounced) or every *tick* seconds."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        tick: float = TICK_SECONDS,
        debounce: float = DEBOUNCE_SECONDS,
        limit: int = IN_PROCESS_LIMIT,
        drain_fn: DrainFn = cls.drain,
    ) -> None:
        """Hold the pool and timings; :meth:`start` begins the loop."""
        self.pool = pool
        self.tick = tick
        self.debounce = debounce
        self.limit = limit
        self.drain_fn = drain_fn
        self.event = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the loop and make this the drainer :func:`wake` reaches."""
        global _current
        self.task = asyncio.create_task(self._loop(), name="classification-drainer")
        _current = self

    async def stop(self) -> None:
        """Cancel the loop and wait for it; :func:`wake` goes quiet."""
        global _current
        if _current is self:
            _current = None
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.event.wait(), timeout=self.tick)
                origin = "wake"
                await asyncio.sleep(self.debounce)
            except TimeoutError:
                origin = "tick"
            self.event.clear()
            await self.run_once(origin)

    async def run_once(self, origin: str) -> cls.DrainResult | None:
        """Run one drain; a failure is logged, never raised."""
        try:
            async with self.pool.acquire() as conn:
                return await self.drain_fn(conn, origin=origin, limit=self.limit)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("classification drain failed (%s)", origin, exc_info=True)
            return None
