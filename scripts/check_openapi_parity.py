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
pushed at the wrong ref is caught as well as a missing one. The repo is public:
the REST API needs no credentials. The check is skipped, with a log line and no
effect on the exit, when ``power-map.service`` entered ``active`` less than
``GRACE`` ago (a deploy minutes before the daily run can't have been tagged
yet), when GitHub can't be reached or answers unexpectedly, and under
``--no-tag-check``.

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

import json
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
# Names listed per line before the rest is counted: a wholesale drift stays readable.
MAX_NAMES = 10

REPO = "CannObserv/power-map"
SNAPSHOT_PATH = "clients/python/openapi.json"
SERVICE = "power-map.service"
# The timer runs once a day; a deploy just before it gets until the next run.
GRACE = timedelta(hours=2)


class TagCheckSkipped(Exception):
    """GitHub could not answer the question; this is not a finding."""


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


def service_started_at(run=subprocess.run) -> datetime | None:
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


def _github_object(opener, url: str, timeout: float) -> dict[str, Any]:
    """The ``object`` of a GitHub ref or annotated tag; raises HTTPError as fetched.

    Every other failure, and any body without a well-formed ``object``, is
    ``TagCheckSkipped``: GitHub's oddity, not a finding.
    """
    try:
        target = _fetch_json(opener, url, timeout)["object"]
    except urllib.error.HTTPError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise TagCheckSkipped(f"GitHub unreachable — {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise TagCheckSkipped(f"GitHub's answer for {url} has no object") from exc
    if not isinstance(target, dict) or not isinstance(target.get("sha"), str):
        raise TagCheckSkipped(f"GitHub's answer for {url} has no object")
    return target


def _tagged_commit(opener, tag: str, timeout: float) -> str | None:
    """The commit *tag* names on GitHub, following an annotated tag; None if absent.

    Only a 404 on the ref itself means absent: the ref exists once it answers.
    """
    api = f"https://api.github.com/repos/{REPO}/git"
    try:
        target = _github_object(opener, f"{api}/ref/tags/{tag}", timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise TagCheckSkipped(f"GitHub answered {exc.code} for {tag}") from exc
    if target.get("type") == "tag":
        try:
            target = _github_object(opener, f"{api}/tags/{target['sha']}", timeout)
        except urllib.error.HTTPError as exc:
            raise TagCheckSkipped(f"GitHub answered {exc.code} for {tag}'s tag object") from exc
    if target.get("type") != "commit":
        raise TagCheckSkipped(f"{tag} does not resolve to a commit")
    return target["sha"]


def release_tag_finding(live: dict[str, Any], opener, timeout: float) -> str | None:
    """Why ``v<live version>`` is not a correct pin, or None when it is.

    Raises ``TagCheckSkipped`` when GitHub can't say either way.
    """
    tag = f"v{live['info']['version']}"
    sha = _tagged_commit(opener, tag, timeout)
    if sha is None:
        return (
            f"{tag} is not tagged on {REPO} — from the main checkout at the deployed "
            f"commit: git tag {tag} && git push origin {tag}"
        )
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}/{SNAPSHOT_PATH}"
    move = (
        f"move it — from the main checkout at the deployed commit: git tag -f {tag} "
        f"&& git push -f origin {tag} (anyone who pinned {tag} has the wrong client)"
    )
    try:
        tagged = _fetch_json(opener, raw, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise TagCheckSkipped(f"GitHub answered {exc.code} for {raw}") from exc
        return f"{tag} points at {sha[:12]}, which has no {SNAPSHOT_PATH}; {move}"
    except ValueError:
        tagged = None
    except (OSError, urllib.error.URLError) as exc:
        raise TagCheckSkipped(f"GitHub unreachable — {exc}") from exc
    if tagged != live:
        return (
            f"{tag} points at {sha[:12]}, whose {SNAPSHOT_PATH} differs from the "
            f"served schema; {move}"
        )
    logger.info("openapi parity: release tag %s at %s matches the served schema", tag, sha[:12])
    return None


def _check_release_tag(live: dict[str, Any], opener, timeout: float, started_at) -> str | None:
    """Run the tag check unless it must be skipped; log every skip at INFO."""
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
        return release_tag_finding(live, opener, timeout)
    except TagCheckSkipped as exc:
        logger.warning("openapi parity: release tag not checked — %s", exc)
        return None


def main(opener=urlopen, started_at: Callable[[], datetime | None] = service_started_at) -> None:
    """CLI entry point; exits 0, 1, 3 or 4 as the module docstring says."""
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
    except (OSError, urllib.error.URLError, ValueError) as exc:
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
        tag_finding = _check_release_tag(live, opener, args.timeout, started_at)
        if tag_finding:
            logger.warning("openapi parity: %s", tag_finding)
    if found:
        sys.exit(3)
    sys.exit(4 if tag_finding else 0)


if __name__ == "__main__":
    main()
