"""Dispatch a failed unit to Notifier; the journal line stays the fallback (issue #566).

The second ``ExecStart=`` of ``infra/power-map-notify-failure@.service``, the
``OnFailure=`` target of ``power-map.service`` (CannObserv/notifier#114). The
first, a ``logger`` crit line, has already run, so nothing here carries the
fallback. The unit prefixes this command with ``-`` too: fail-open is enforced
by systemd, not only by the catch-all in ``main()``.

One dispatch per failed invocation: ``idempotency_key = {unit}:{invocation_id}``,
so a handler retried for the same failure sends once. ``power-map.service`` runs
``RestartMode=direct`` under a start limit, so a crash loop reaches ``failed``,
and this handler, once, when the loop gives up.

The variables are a summary and the journal command, **never journal text**:
this repo is public, and alert bodies reach places it does not control. The
wording is the stored template in ``scripts/notifier/unit-failure.json``,
published by ``scripts/publish_notifier_template.py``.

Usage:
    notify_unit_failure.py <unit>

Environment (the handler unit's ``Environment=``; none are secrets):
    NOTIFIER_URL           http://notifier:9000; unset = journal only
    NOTIFIER_TEMPLATE_ID   the stored template's ULID; unset = journal only
    NOTIFIER_CHANNEL_IDS   comma-separated channel ULIDs; unset = journal only
    CREDENTIALS_DIRECTORY  systemd's; holds ``notifier-api``, the tenant key

Set by systemd (>= 251) for an ``OnFailure=`` handler; absent on a manual
``systemctl start power-map-notify-failure@<unit>.service``:
    MONITOR_SERVICE_RESULT, MONITOR_EXIT_CODE, MONITOR_EXIT_STATUS, MONITOR_INVOCATION_ID
"""

import asyncio
import os
import socket
import sys
import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from notifier_client import NotifierClient, NotifierError, RetryConfig

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

CREDENTIAL = "notifier-api"
TEMPLATE_PATH = Path(__file__).resolve().parent / "notifier" / "unit-failure.json"
EXPECTED_ENVIRONMENT = "production"
JOURNAL_LINES = 100
# Notifier delivers to every channel before it answers, so the read covers the
# Slack and Mailgun round trips; a read timeout on a delivered dispatch would log
# a false failure and retry into the request still in flight.
REQUEST_TIMEOUT_SECONDS = 30.0
RETRY = RetryConfig(max_attempts=2)
# httpx timeouts are per phase, so attempts alone do not bound a run. This does,
# inside the unit's TimeoutStartSec=.
RUN_BUDGET_SECONDS = 60


@dataclass(frozen=True)
class NotifierConfig:
    """Where and how to dispatch. Built only when every piece is provisioned."""

    url: str
    api_key: str
    template_id: str
    channel_ids: list[str]


@dataclass(frozen=True)
class Failure:
    """One failed invocation, and the key that makes its dispatch send once."""

    unit: str
    result: str
    invocation_id: str | None
    idempotency_key: str


def _read_key(env: Mapping[str, str]) -> str:
    directory = env.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        return ""
    try:
        return (Path(directory) / CREDENTIAL).read_text().strip()
    except OSError:
        return ""


def load_config(env: Mapping[str, str]) -> NotifierConfig | None:
    """The dispatch config, or None (journal only) while any piece is unprovisioned."""
    url = env.get("NOTIFIER_URL", "").strip()
    template_id = env.get("NOTIFIER_TEMPLATE_ID", "").strip()
    channel_ids = [c.strip() for c in env.get("NOTIFIER_CHANNEL_IDS", "").split(",") if c.strip()]
    missing = [
        name
        for name, value in (
            ("NOTIFIER_URL", url),
            ("NOTIFIER_TEMPLATE_ID", template_id),
            ("NOTIFIER_CHANNEL_IDS", channel_ids),
        )
        if not value
    ]
    if missing:
        logger.warning("%s unset; the failure is reported to the journal only", ", ".join(missing))
        return None
    api_key = _read_key(env)
    if not api_key:
        logger.warning("no %s key in CREDENTIALS_DIRECTORY; journal only", CREDENTIAL)
        return None
    return NotifierConfig(url, api_key, template_id, channel_ids)


def failure_from_env(unit: str, env: Mapping[str, str]) -> Failure:
    """The failure systemd describes, or a ``manual`` one with a fresh key."""
    invocation_id = env.get("MONITOR_INVOCATION_ID") or None
    result = env.get("MONITOR_SERVICE_RESULT") or "manual"
    exit_code = env.get("MONITOR_EXIT_CODE") or ""
    exit_status = env.get("MONITOR_EXIT_STATUS") or ""
    exit_detail = f"{exit_code} {exit_status}".strip()
    return Failure(
        unit=unit,
        result=f"{result} ({exit_detail})" if exit_detail else result,
        invocation_id=invocation_id,
        # A replayed key returns the earlier dispatch and delivers nothing, so a
        # manual trigger, which has no invocation id, gets a key of its own.
        idempotency_key=(
            f"{unit}:{invocation_id}" if invocation_id else f"{unit}:manual:{uuid.uuid4()}"
        ),
    )


async def notify(
    config: NotifierConfig,
    failure: Failure,
    *,
    host: str,
    client_factory: Callable[..., Any] = NotifierClient,
) -> str | None:
    """Dispatch the failure; return the delivery status, or None if nothing was sent."""
    async with client_factory(
        base_url=config.url,
        api_key=config.api_key,
        retry_config=RETRY,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as client:
        environment = (await client.health()).get("environment")
        if environment != EXPECTED_ENVIRONMENT:
            logger.warning(
                "notifier at %s reports environment=%s, expected %s; not dispatching",
                config.url,
                environment,
                EXPECTED_ENVIRONMENT,
            )
            return None
        result = await client.dispatch(
            template_id=config.template_id,
            channel_ids=config.channel_ids,
            variables={
                "unit": failure.unit,
                "result": failure.result,
                "host": host,
                "journal_cmd": f"journalctl -u {failure.unit} -n {JOURNAL_LINES}",
            },
            idempotency_key=failure.idempotency_key,
            metadata={
                "source": "power-map",
                "event": "unit-failure",
                "unit": failure.unit,
                "invocation_id": failure.invocation_id,
            },
        )
    # A 202 is not a delivery: judge by the body's status (notifier#70).
    status = str(result.status)
    if status == "succeeded":
        logger.info("notifier dispatch %s for %s: succeeded", result.id, failure.unit)
    else:
        logger.warning(
            "notifier dispatch %s for %s: %s (%d of %d channels delivered)",
            result.id,
            failure.unit,
            status,
            sum(str(a.status) == "succeeded" for a in result.attempts),
            len(result.attempts),
        )
    return status


async def _within_budget(coro: Coroutine[Any, Any, str | None]) -> str | None:
    async with asyncio.timeout(RUN_BUDGET_SECONDS):
        return await coro


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[..., Any] = NotifierClient,
) -> int:
    """Always 0: a failing handler would itself need a failure handler."""
    args = sys.argv[1:] if argv is None else argv
    environ = os.environ if env is None else env
    if len(args) != 1:
        logger.warning("usage: notify_unit_failure.py <unit>")
        return 0
    unit = args[0]
    try:
        config = load_config(environ)
        if config is None:
            return 0
        failure = failure_from_env(unit, environ)
        asyncio.run(
            _within_budget(
                notify(config, failure, host=socket.gethostname(), client_factory=client_factory)
            )
        )
    except (NotifierError, httpx.HTTPError, TimeoutError) as exc:
        logger.warning(
            "notifier dispatch for %s failed (%s: %s); the journal line stands",
            unit,
            type(exc).__name__,
            getattr(exc, "status_code", None) or exc,
        )
    except Exception:  # fail-open: this is the alert path's last resort
        logger.warning(
            "notifier dispatch for %s failed unexpectedly; the journal line stands",
            unit,
            exc_info=True,
        )
    return 0


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
