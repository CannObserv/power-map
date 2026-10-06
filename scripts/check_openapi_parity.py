"""Live OpenAPI parity guard (#618): the served schema against the committed one.

Wired to ``infra/power-map-openapi-parity.timer`` (daily). The gate for the
schema is the unit test ``tests/clients/python/test_drift.py``: a change to the
public schema cannot be committed without regenerating ``clients/python``. That
gate runs in-process at commit time. This backstop compares what the production
workers actually serve at ``/openapi.json`` with ``clients/python/openapi.json``
in the main checkout, the deployed commit. It catches what the gate cannot see:
a schema that depends on runtime configuration, a deploy that skipped the gate,
and a pull that was never followed by a restart (``info.version`` then differs).

HTTP-only and read-only: no database, no writes, no privilege. It does not open
a GitHub issue. A difference shows in ``systemctl --failed``, and #566's alerting
will carry it once that lands.

Exit codes: 0 = identical; 3 = the live schema differs (the journal names what);
1 = the schema could not be fetched or read (``/ready``'s guard owns liveness).

Usage:
    uv run python -m scripts.check_openapi_parity
    uv run python -m scripts.check_openapi_parity --url http://localhost:8001/openapi.json
"""

import json
import sys
import urllib.error
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


def main(opener=urlopen) -> None:
    """CLI entry point; exits 0, 1 or 3 as the module docstring says."""
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
    if not found:
        logger.info("openapi parity: %s matches %s", args.url, args.snapshot)
        sys.exit(0)
    for line in found:
        logger.warning("openapi parity: %s", line)
    logger.warning(
        "openapi parity: %s differs from %s — restart if a pull was not followed by "
        "one, otherwise find the change that bypassed the snapshot gate",
        args.url,
        args.snapshot,
    )
    sys.exit(3)


if __name__ == "__main__":
    main()
