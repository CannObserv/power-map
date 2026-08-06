"""Shared connection targeting for operational scripts (#402, #399).

`DATABASE_URL` resolves to **production** from any directory, so a script that
prints nothing about where it connected leaves no way to tell which database it
touched. Scripts call `add_dsn_args(parser)` + `resolve_dsn(args, parser)`,
which resolve the target and echo it, labelled, before the first connection.

Two groups of function, with **different failure contracts**:

* Diagnostics — `redact_dsn`, `describe_dsn`, `echo_target`, `default_dsn`.
  These never raise on a caller's behalf. A DSN that is not a parseable URL is
  reported as unredactable and **never echoed**; callers must not fall back to
  the raw string.
* Resolution — `resolve_dsn`. This is a CLI entry point and **exits** via
  `parser.error()` (SystemExit, status 2) on a usage error: no DSN available,
  `--test` without `TEST_DATABASE_URL`, or two targets named at once. Do not
  call it from anywhere that must survive bad input.

Rationale, the label's `(host, port, dbname)` keying, the two dry-run shapes,
and why `apply-schema.sh` keeps its own copy → `docs/CONVENTIONS.md`
§"Operational scripts — dry run by default & target echo".
"""

import argparse
import os
import sys
from dataclasses import dataclass
from typing import TextIO
from urllib.parse import urlparse

__all__ = [
    "PRODUCTION",
    "TEST",
    "UNKNOWN",
    "Target",
    "add_dsn_args",
    "default_dsn",
    "describe_dsn",
    "echo_target",
    "redact_dsn",
    "resolve_dsn",
]

UNREDACTABLE = "(unparsed DSN — cannot redact)"

PRODUCTION = "production"
TEST = "test"
# Never "probably fine": an unrecognised target is announced as one that might
# be production, because the consequence of guessing wrong runs one way.
UNKNOWN = "unknown — assume production"

# Both production DSNs describe one database as different users, so the label
# cannot key on the DSN string.
_PRODUCTION_VARS = ("DATABASE_URL", "MIGRATIONS_DATABASE_URL")
_TEST_VARS = ("TEST_DATABASE_URL",)

_DEFAULT_PORT = 5432


def redact_dsn(dsn: str | None) -> str | None:
    """Return ``user@host:port/dbname`` for *dsn*, or None if it is not a URL.

    The password and the query string are dropped — the query string can carry
    credentials of its own. None means "do not print anything derived from
    this": callers must not fall back to the raw string.
    """
    if not dsn:
        return None
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        # Non-numeric port — the DSN is malformed; say nothing about it.
        return None

    user = f"{parsed.username}@" if parsed.username else ""
    tail = f":{port}" if port else ""
    database = (parsed.path or "").lstrip("/")
    # `?` for an absent database name, matching apply-schema.sh (parity-tested).
    return f"{user}{parsed.hostname}{tail}/{database or '?'}"


def _identity(dsn: str | None) -> tuple[str, int, str] | None:
    """Return ``(host, port, dbname)`` — what makes two DSNs the same database.

    User, password and query string are excluded on purpose: production is
    reached as both the app user and the migrations user, and a label that
    disagreed with itself depending on which one you used would be worse than
    no label.
    """
    if redact_dsn(dsn) is None:
        return None
    parsed = urlparse(dsn)
    return (
        (parsed.hostname or "").lower(),
        parsed.port or _DEFAULT_PORT,
        (parsed.path or "").lstrip("/"),
    )


@dataclass(frozen=True)
class Target:
    """A connection target: what to print for it, and which database it is."""

    redacted: str | None
    label: str

    def __str__(self) -> str:
        return f"{self.redacted or UNREDACTABLE} ({self.label})"


def describe_dsn(dsn: str | None) -> Target:
    """Classify *dsn* as production, test, or unknown, with a redacted rendering.

    Unrecognised targets — including any DSN that will not parse — come back
    `UNKNOWN`, which reads as "assume production". Nothing here reads a DSN
    the caller did not supply beyond the environment variables used for
    comparison.
    """
    identity = _identity(dsn)
    label = UNKNOWN
    if identity is not None:
        for var in _PRODUCTION_VARS:
            if _identity(os.environ.get(var)) == identity:
                label = PRODUCTION
                break
        else:
            for var in _TEST_VARS:
                if _identity(os.environ.get(var)) == identity:
                    label = TEST
                    break
    return Target(redacted=redact_dsn(dsn), label=label)


def echo_target(dsn: str | None, *, role: str = "target", stream: TextIO | None = None) -> None:
    """Announce the connection target on stderr, redacted and labelled.

    Stderr rather than stdout so the line survives a piped or redirected run
    and cannot corrupt a script's real output. Multi-DSN scripts pass a *role*
    (`"target"` / `"reference"`) so each connection gets its own line.
    """
    print(f"{role}: {describe_dsn(dsn)}", file=stream or sys.stderr)


def default_dsn() -> str | None:
    """`DATABASE_URL`, or None when unset or empty.

    For the handful of scripts whose target flags are domain-named
    (`audit_schema_constraint_parity`'s `--target-url` / `--reference-url`) and
    so cannot take `add_dsn_args`. They still echo via `echo_target`; this keeps
    the environment-variable name in one module.
    """
    return os.environ.get("DATABASE_URL") or None


def add_dsn_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the uniform target-selection flags. Pair with `resolve_dsn`.

    The defaults are deliberately *not* resolved here — `resolve_dsn` reads the
    environment at call time so a test can set it after the parser is built.
    """
    parser.add_argument(
        "--database-url",
        default=None,
        help="DSN to connect to (default: DATABASE_URL — production).",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Connect to TEST_DATABASE_URL instead. Errors if it is unset.",
    )
    return parser


def resolve_dsn(
    args: argparse.Namespace, parser: argparse.ArgumentParser, *, echo: bool = True
) -> str:
    """Resolve the target DSN from `add_dsn_args` flags, echoing it by default.

    `--test` **never** falls back to `DATABASE_URL`: a test flag that silently
    reached production would be a worse version of the bug this module exists
    to prevent, so an unset (or empty) `TEST_DATABASE_URL` is a usage error.
    Naming two targets at once is an error rather than a precedence rule.
    """
    wants_test = getattr(args, "test", False)
    explicit = getattr(args, "database_url", None)

    if wants_test and explicit:
        parser.error("--test and --database-url name two different targets; pass one")

    if wants_test:
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn:
            parser.error("--test requires TEST_DATABASE_URL; refusing to fall back to DATABASE_URL")
    else:
        dsn = explicit or os.environ.get("DATABASE_URL")
        if not dsn:
            parser.error("no database URL: pass --database-url or set DATABASE_URL")

    if echo:
        echo_target(dsn)
    return dsn
