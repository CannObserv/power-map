"""Live OpenAPI parity guard (#618): the served schema against the committed one.

Wired to ``infra/power-map-openapi-parity.timer`` (daily). The gate for the
schema is the unit test ``tests/clients/python/test_drift.py``: a change to the
public schema cannot be committed without regenerating ``clients/python``. That
gate runs in-process at commit time. This backstop compares what the production
workers actually serve at ``/openapi.json`` with ``clients/python/openapi.json``
in the main checkout, the deployed commit. It catches what the gate cannot see:
a schema that depends on runtime configuration, a deploy that skipped the gate,
and a pull that was never followed by a restart (``info.version`` then differs).

It also checks the release tag (#631), the pin ``power-map-client`` consumers
install from. ``v<served info.version>`` must exist on GitHub and point at a
commit whose ``clients/python/openapi.json`` equals the served schema, so a tag
pushed at the wrong ref is caught as well as a missing one. The repo is public,
so neither call needs credentials. The tag is resolved with one anonymous
``git ls-remote`` over https (#824), not the REST API: that quota (60 an hour)
belongs to the egress IP, which other consumers share, and once spent, the check
went dark. The snapshot comes from ``raw.githubusercontent.com``. The check is
skipped, with a log line and no effect on the exit, when ``power-map.service``
entered ``active`` less than ``GRACE`` ago (a deploy minutes before the daily
run can't have been tagged yet), when git or GitHub can't answer or answers
unexpectedly, and under ``--no-tag-check``.

HTTP-only and read-only: no database, no writes, no privilege. It does not open
a GitHub issue. A difference shows in ``systemctl --failed``, and #566's alerting
will carry it once that lands.

Exit codes: 0 = identical and tagged; 3 = the live schema differs (the journal
names what), which outranks 4; 4 = the served version is untagged or mis-tagged
(the journal names the fix); 1 = the schema could not be fetched or read
(``/ready``'s guard owns liveness).

Usage:
    uv run python -m scripts.check_openapi_parity
    uv run python -m scripts.check_openapi_parity --no-tag-check \
        --url http://localhost:8001/openapi.json
"""

import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from scripts._dsn import build_parser
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_URL = "http://localhost:8000/openapi.json"
DEFAULT_SNAPSHOT = Path(__file__).resolve().parents[1] / "clients" / "python" / "openapi.json"
DEFAULT_TIMEOUT = 10.0
# What a failed fetch can raise. urllib wraps OSError only around sending: a
# response that breaks off mid-read raises http.client.HTTPException, unwrapped.
FETCH_ERRORS = (OSError, urllib.error.URLError, http.client.HTTPException)

# Names listed per line before the rest is counted: a wholesale drift stays readable.
MAX_NAMES = 10

REPO = "CannObserv/power-map"
# Over https, never ``origin``: in the main checkout that is SSH and needs the deploy key.
GIT_URL = f"https://github.com/{REPO}"
# A SHA-1 or SHA-256 object name, as ``git ls-remote`` prints it.
OBJECT_NAME = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
SNAPSHOT_PATH = "clients/python/openapi.json"
SERVICE = "power-map.service"
# The timer runs once a day; a deploy just before it gets until the next run.
GRACE = timedelta(hours=2)


class TagCheckSkipped(Exception):
    """git or GitHub could not answer the question; this is not a finding."""


def _names(keys: list[str]) -> str:
    shown = ", ".join(keys[:MAX_NAMES])
    return f"{shown} (+{len(keys) - MAX_NAMES} more)" if len(keys) > MAX_NAMES else shown


def _keyed_differences(label: str, live: dict, committed: dict) -> list[str]:
    """Name the keys of one mapping that are only on one side, or differ."""
    found = []
    only_live = sorted(live.keys() - committed.keys())
    only_committed = sorted(committed.keys() - live.keys())
    changed = sorted(k for k in live.keys() & committed.keys() if live[k] != committed[k])
    if only_live:
        found.append(f"{label} only live: {_names(only_live)}")
    if only_committed:
        found.append(f"{label} only committed: {_names(only_committed)}")
    if changed:
        found.append(f"{label} differ: {_names(changed)}")
    return found


def differences(live: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    """One line per difference, most useful first; empty when the two are equal."""
    if live == committed:
        return []
    found = []
    live_version = live.get("info", {}).get("version")
    committed_version = committed.get("info", {}).get("version")
    if live_version != committed_version:
        found.append(f"info.version: live {live_version}, committed {committed_version}")
    found += _keyed_differences("paths", live.get("paths", {}), committed.get("paths", {}))
    found += _keyed_differences(
        "components.schemas",
        live.get("components", {}).get("schemas", {}),
        committed.get("components", {}).get("schemas", {}),
    )
    explained = {"paths", "components"}
    rest = sorted(
        k
        for k in live.keys() | committed.keys()
        if k not in explained and k != "info" and live.get(k) != committed.get(k)
    )
    if rest:
        found.append(f"top-level keys differ: {_names(rest)}")
    if not found:
        found.append("schemas differ outside info.version, paths and components.schemas")
    return found


def service_started_at(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> datetime | None:
    """When ``power-map.service`` last entered ``active``; None when unknown."""
    cmd = [
        "systemctl",
        "show",
        SERVICE,
        "--property=ActiveEnterTimestamp",
        "--timestamp=unix",
        "--value",
    ]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or not value.startswith("@"):
        return None
    try:
        return datetime.fromtimestamp(int(value[1:]), UTC)
    except ValueError:
        return None


def _fetch_json(opener, url: str, timeout: float) -> Any:
    with opener(url, timeout=timeout) as response:
        return json.loads(response.read())


def git_env() -> dict[str, str]:
    """This process's environment for a ``git ls-remote`` that nothing local can steer.

    Inherited ``GIT_*`` (a hook's ``GIT_DIR``, ``GIT_CONFIG_PARAMETERS``) go; a
    credential prompt fails instead of hanging the unit; and no system or global
    config is read, so a ``url.<base>.insteadOf`` can't rewrite https to SSH.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no message"


def tagged_commit(
    tag: str,
    timeout: float,
    git: Callable[..., subprocess.CompletedProcess],
    url: str = GIT_URL,
) -> str | None:
    """The commit *tag* names at *url*, peeling an annotated tag; None if absent.

    *git* is ``subprocess.run`` or a test's stand-in. One ``git ls-remote`` asks
    for the tag and its peel: an annotated tag answers both lines and the
    ``^{}`` one is the commit, a lightweight tag only the first. ls-remote's
    patterns are globs matched on the ref's tail, so only exact names count.
    Raises ``TagCheckSkipped`` when git fails, times out, isn't there, or prints
    what it never prints.
    """
    ref = f"refs/tags/{tag}"
    peeled = f"{ref}^{{}}"
    cmd = ["git", "ls-remote", "--exit-code", url, ref, peeled]
    try:
        result = git(cmd, capture_output=True, text=True, timeout=timeout, env=git_env(), cwd="/")
    except subprocess.TimeoutExpired as exc:
        raise TagCheckSkipped(f"git ls-remote timed out after {timeout:g} s for {tag}") from exc
    except OSError as exc:
        raise TagCheckSkipped(f"git could not run — {exc}") from exc
    if result.returncode == 2:  # --exit-code: no ref matched either pattern
        return None
    if result.returncode != 0:
        raise TagCheckSkipped(
            f"git ls-remote exited {result.returncode} for {tag} — {_last_line(result.stderr)}"
        )
    found = {}
    for line in result.stdout.splitlines():
        name, tab, refname = line.partition("\t")
        if not tab or not OBJECT_NAME.fullmatch(name):
            raise TagCheckSkipped(f"git ls-remote printed an unreadable line for {tag}")
        if refname in (ref, peeled):
            found[refname] = name
    if ref not in found:
        if peeled in found:
            raise TagCheckSkipped(f"git ls-remote printed {tag}'s peel without the tag")
        return None
    return found.get(peeled, found[ref])


def release_tag_finding(
    live: dict[str, Any],
    opener,
    timeout: float,
    git: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Why ``v<live version>`` is not a correct pin, or None when it is.

    *live* must carry ``info.version`` (``KeyError`` otherwise; the CLI checks
    first). Raises ``TagCheckSkipped`` when git or GitHub can't say either way.
    The remedies name an annotated tag, the release rule; a lightweight tag on
    the right commit still passes.
    """
    tag = f"v{live['info']['version']}"
    sha = tagged_commit(tag, timeout, git)
    if sha is None:
        return (
            f"{tag} is not tagged on {REPO} — from the main checkout at the deployed "
            f"commit: git tag -a {tag} -m {tag} && git push origin {tag}"
        )
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}/{SNAPSHOT_PATH}"
    move = (
        f"move it — from the main checkout at the deployed commit: git tag -a -f {tag} "
        f"-m {tag} && git push -f origin {tag} (anyone who pinned {tag} has the wrong client)"
    )
    try:
        tagged = _fetch_json(opener, raw, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise TagCheckSkipped(f"GitHub answered {exc.code} for {raw}") from exc
        return f"{tag} points at {sha[:12]}, which has no {SNAPSHOT_PATH}; {move}"
    except ValueError:
        tagged = None
    except FETCH_ERRORS as exc:
        raise TagCheckSkipped(f"GitHub unreachable — {exc}") from exc
    if tagged != live:
        return (
            f"{tag} points at {sha[:12]}, whose {SNAPSHOT_PATH} differs from the "
            f"served schema; {move}"
        )
    logger.info("openapi parity: release tag %s at %s matches the served schema", tag, sha[:12])
    return None


def _check_release_tag(
    live: dict[str, Any],
    opener,
    timeout: float,
    started_at: Callable[[], datetime | None],
    git: Callable[..., subprocess.CompletedProcess],
) -> str | None:
    """Run the tag check unless it must be skipped.

    A skip by design (no version, inside the grace window) logs INFO; git or
    GitHub trouble logs WARNING, since the check was wanted and did not happen.
    """
    version = live.get("info", {}).get("version") if isinstance(live.get("info"), dict) else None
    if not version:
        logger.info("openapi parity: release tag not checked — the schema has no info.version")
        return None
    started = started_at()
    if started is not None and datetime.now(UTC) - started < GRACE:
        logger.info(
            "openapi parity: release tag not checked — %s started %s, inside the %g h grace "
            "window after a deploy",
            SERVICE,
            started.isoformat(),
            GRACE / timedelta(hours=1),
        )
        return None
    try:
        return release_tag_finding(live, opener, timeout, git)
    except TagCheckSkipped as exc:
        logger.warning("openapi parity: release tag not checked — %s", exc)
        return None


def main(
    opener=urlopen,
    started_at: Callable[[], datetime | None] = service_started_at,
    git: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """CLI entry point; exits 0, 1, 3 or 4 as the module docstring says.

    *opener* fetches over HTTP, *git* runs ``git ls-remote``: both are seams for tests.
    """
    configure_logging()
    parser = build_parser(__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"live schema (default {DEFAULT_URL})")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="committed schema (default clients/python/openapi.json beside this script)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds")
    parser.add_argument(
        "--no-tag-check",
        action="store_true",
        help="skip the release-tag check (#631), e.g. against a dev server",
    )
    args = parser.parse_args()
    # urlopen raises ValueError on these, which would read as "could not fetch".
    if args.timeout <= 0:
        parser.error(f"--timeout must be greater than 0 (got {args.timeout})")

    try:
        with opener(args.url, timeout=args.timeout) as response:
            live = json.loads(response.read())
        committed = json.loads(args.snapshot.read_text())
    except (*FETCH_ERRORS, ValueError) as exc:
        logger.error("openapi parity: could not fetch or read a schema — %s", exc)
        sys.exit(1)
    for label, document in (("live", live), ("committed", committed)):
        if not isinstance(document, dict):
            logger.error(
                "openapi parity: the %s schema is not a JSON object (%s)",
                label,
                type(document).__name__,
            )
            sys.exit(1)

    found = differences(live, committed)
    if found:
        for line in found:
            logger.warning("openapi parity: %s", line)
        logger.warning(
            "openapi parity: %s differs from %s — restart if a pull was not followed by "
            "one, otherwise find the change that bypassed the snapshot gate",
            args.url,
            args.snapshot,
        )
    else:
        logger.info("openapi parity: %s matches %s", args.url, args.snapshot)
    tag_finding = None
    if not args.no_tag_check:
        tag_finding = _check_release_tag(live, opener, args.timeout, started_at, git)
        if tag_finding:
            logger.warning("openapi parity: %s", tag_finding)
    if found:
        sys.exit(3)
    sys.exit(4 if tag_finding else 0)


if __name__ == "__main__":
    main()
