"""Readiness uptime guard (issue #347) — polls ``/ready`` and surfaces failures.

Wired to ``infra/power-map-ready.timer`` (every 2 minutes). Exists because
``/ready`` has been correct and unread: during the 2026-08-09 outage it
returned ``503 {"reason": "pool_timeout"}`` from 11:29:49 until the fix, while
detection was a human hitting a white screen ~30 minutes later. systemd's
``Restart=on-failure`` never engaged — the process was healthy, only its
database was unreachable.

HTTP-only: this script never opens a database connection, so there is no
``_dsn`` layer here. It probes ``localhost`` deliberately (#347 option 1) —
the failure class it exists for lives in the app→DB direction, which a local
probe sees perfectly. An external monitor against the public proxy would add
proxy-layer coverage; that is a separate, additive job.

Two behaviours are load-bearing and easy to get wrong:

- **Flap resistance.** A single failed probe is not an outage. The run retries
  after ``--retry-delay`` and only alerts when every attempt fails, so a lone
  2-second blip stays quiet.
- **Quiet when already alerting.** At a 2-minute cadence, commenting on each
  failing run would post ~30 comments an hour. An already-open issue is left
  untouched; recovery comments once and closes it. No local state file is
  needed — the open issue *is* the state, held by GitHub.

Exit codes: 0 = ready; 3 = not ready (systemd marks the unit failed, so it
shows in ``systemctl --failed``, the ambient signal the SessionStart hook
reads). Exit 3, not 2, keeps it distinct from argparse usage errors.

Test hatches:
    READY_CHECK_NO_GH=1      skip GitHub surfacing entirely.
    READY_CHECK_FORCE_FAIL=1 skip probing and exercise the failure path.

Usage:
    uv run python -m scripts.check_ready
    uv run python -m scripts.check_ready --url http://localhost:8001/ready --no-gh
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.request import urlopen

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_URL = "http://localhost:8000/ready"
# /ready bounds itself at ~4s (2s pool acquire + 2s probe query); the client
# timeout must outlast that or the guard reports its own impatience as an outage.
DEFAULT_TIMEOUT = 10.0
DEFAULT_ATTEMPTS = 2
DEFAULT_RETRY_DELAY = 10.0

LABEL = "ready-regression"
ISSUE_TITLE = "Readiness probe failing (automated)"


@dataclass(frozen=True)
class ProbeResult:
    """One probe of ``/ready``. ``reason`` is the triage slug, absent when ok."""

    ok: bool
    status: int | None
    reason: str | None


def _reason_from_body(body: bytes) -> str:
    """Pull ``reason`` out of a 503 payload; degrade rather than raise."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return "unknown"
    reason = payload.get("reason") if isinstance(payload, dict) else None
    return reason if isinstance(reason, str) and reason else "unknown"


def probe(url: str, *, timeout: float, opener=None) -> ProbeResult:
    """Probe ``url`` once and classify the outcome.

    ``urlopen`` raises ``HTTPError`` for 503 rather than returning it, and
    ``HTTPError`` doubles as a readable response — hence the ordering below
    (``HTTPError`` before its parent ``URLError``).
    """
    opener = opener or urlopen
    try:
        with opener(url, timeout=timeout) as response:
            status = getattr(response, "status", None)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = getattr(exc, "status", None) or exc.code
        try:
            body = exc.read()
        except Exception:  # pragma: no cover - defensive; body already consumed
            body = b""
        if status == 503:
            return ProbeResult(ok=False, status=status, reason=_reason_from_body(body))
        return ProbeResult(ok=False, status=status, reason=f"http_{status}")
    except TimeoutError:
        return ProbeResult(ok=False, status=None, reason="probe_timeout")
    except urllib.error.URLError as exc:
        # A timeout can arrive wrapped rather than raised directly.
        if isinstance(exc.reason, TimeoutError):
            return ProbeResult(ok=False, status=None, reason="probe_timeout")
        return ProbeResult(ok=False, status=None, reason="unreachable")
    except OSError:
        return ProbeResult(ok=False, status=None, reason="unreachable")

    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        payload = {}
    if isinstance(payload, dict) and payload.get("status") == "ok":
        return ProbeResult(ok=True, status=status, reason=None)
    return ProbeResult(ok=False, status=status, reason="unexpected_body")


def check(
    url: str,
    *,
    attempts: int,
    retry_delay: float,
    timeout: float,
    opener=None,
    sleep=time.sleep,
) -> list[ProbeResult]:
    """Probe until one succeeds or ``attempts`` are spent.

    Returns every result taken. The caller reads the last one: a failure
    followed by a success is a blip, and blips are green.
    """
    results: list[ProbeResult] = []
    for attempt in range(attempts):
        result = probe(url, timeout=timeout, opener=opener)
        results.append(result)
        if result.ok:
            break
        if attempt < attempts - 1:
            sleep(retry_delay)
    return results


def summarize(results: list[ProbeResult]) -> str:
    """One line for the journal and the issue body — slug first, no internals."""
    last = results[-1]
    where = f"HTTP {last.status}" if last.status is not None else "no response"
    return f"/ready not ready after {len(results)} attempt(s) — {where}, reason `{last.reason}`"


def _gh(args: list[str]) -> tuple[int, str]:
    """Run ``gh`` with ``args``; return (returncode, stdout)."""
    completed = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout


def _ensure_label(runner) -> None:
    """Create the label if absent. Best-effort — never blocks surfacing."""
    rc, out = runner(["label", "list", "--limit", "200", "--json", "name", "-q", ".[].name"])
    if rc == 0 and LABEL in out.split():
        return
    runner(
        [
            "label",
            "create",
            LABEL,
            "--color",
            "B60205",
            "--description",
            "Automated /ready probe failure (power-map-ready.timer, #347)",
        ]
    )


def surface(ok: bool, summary: str, *, runner=None) -> None:
    """Open the alert issue on failure, close it on recovery.

    Deliberately quiet when an issue is already open: this runs every two
    minutes, so a comment per failing run would bury the signal it exists to
    raise. Best-effort throughout — surfacing must never change the exit code.
    """
    runner = runner or _gh
    try:
        _ensure_label(runner)
        rc, out = runner(
            [
                "issue",
                "list",
                "--label",
                LABEL,
                "--state",
                "open",
                "--json",
                "number",
                "-q",
                ".[0].number",
            ]
        )
        if rc != 0:
            # Distinguishing "none open" from "gh failed" matters: acting on a
            # failed list risks a duplicate issue (a11y #369 CR2 finding 5).
            logger.warning("gh issue list failed (rc=%d) — skipping surfacing this run", rc)
            return
        existing = out.strip()
        if existing in ("", "null"):
            existing = None

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not ok:
            if existing:
                logger.info("readiness alert already open as #%s — staying quiet", existing)
                return
            body = (
                f"**Readiness probe FAILING** — {timestamp}.\n\n{summary}\n\n"
                "Full detail is on the VM: `journalctl -u power-map-ready` "
                "(deliberately not published — this is a public repo). "
                "Automated by `power-map-ready.timer` (#347)."
            )
            runner(["issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body", body])
            logger.warning("opened %s issue", LABEL)
        elif existing:
            runner(
                [
                    "issue",
                    "comment",
                    existing,
                    "--body",
                    f"✅ **Recovered** — `/ready` green as of {timestamp}. Auto-closing.",
                ]
            )
            runner(["issue", "close", existing])
            logger.info("closed recovered readiness issue #%s", existing)
    except Exception:
        # A broken gh must not turn a real outage into a clean exit.
        logger.warning("GitHub surfacing failed — probe result stands", exc_info=True)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def main() -> None:
    """CLI entry point — exits 3 when the service is not ready."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Probe /ready and surface failures (#347)")
    parser.add_argument(
        "--url",
        default=os.environ.get("READY_PROBE_URL", DEFAULT_URL),
        help=f"readiness URL (default {DEFAULT_URL}; env READY_PROBE_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("READY_PROBE_TIMEOUT", DEFAULT_TIMEOUT),
        help=f"per-probe timeout in seconds (default {DEFAULT_TIMEOUT}; env READY_PROBE_TIMEOUT)",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=_env_int("READY_PROBE_ATTEMPTS", DEFAULT_ATTEMPTS),
        help=(
            f"probes before declaring failure "
            f"(default {DEFAULT_ATTEMPTS}; env READY_PROBE_ATTEMPTS)"
        ),
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=_env_float("READY_PROBE_RETRY_DELAY", DEFAULT_RETRY_DELAY),
        help=(
            f"seconds between attempts (default {DEFAULT_RETRY_DELAY}; env READY_PROBE_RETRY_DELAY)"
        ),
    )
    parser.add_argument(
        "--no-gh", action="store_true", help="skip GitHub surfacing (env READY_CHECK_NO_GH)"
    )
    args = parser.parse_args()
    no_gh = args.no_gh or bool(os.environ.get("READY_CHECK_NO_GH"))

    if os.environ.get("READY_CHECK_FORCE_FAIL"):
        logger.warning("READY_CHECK_FORCE_FAIL set — exercising the failure path (synthetic)")
        if not no_gh:
            surface(
                False, "synthetic failure (READY_CHECK_FORCE_FAIL) — surfacing self-test, ignore."
            )
        sys.exit(3)

    results = check(
        args.url,
        attempts=args.attempts,
        retry_delay=args.retry_delay,
        timeout=args.timeout,
        opener=urlopen,
    )
    ok = results[-1].ok
    if ok:
        logger.info("readiness OK (%s, %d attempt(s))", args.url, len(results))
    else:
        logger.warning("readiness FAILED — %s (%s)", summarize(results), args.url)

    if not no_gh:
        surface(ok, summarize(results))
    if not ok:
        sys.exit(3)


if __name__ == "__main__":
    main()
