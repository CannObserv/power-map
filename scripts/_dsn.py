"""Shared connection-target diagnostics for operational scripts (#402).

`DATABASE_URL` resolves to **production** from any directory, so a script that
prints nothing about where it connected leaves no way to tell which database it
touched. Call `echo_target(dsn)` before opening a connection.

Every function here is diagnostic and none may raise on a caller's behalf. A
DSN that is not a parseable URL is reported as unredactable and **never
echoed** — callers must not fall back to the raw string.

Rationale, the two dry-run shapes, and why `apply-schema.sh` keeps its own copy
→ `docs/CONVENTIONS.md` §"Operational scripts — dry run by default & target
echo".
"""

import sys
from typing import TextIO
from urllib.parse import urlparse

__all__ = ["echo_target", "redact_dsn"]

UNREDACTABLE = "(unparsed DSN — cannot redact)"


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


def echo_target(dsn: str | None, *, role: str = "target", stream: TextIO | None = None) -> None:
    """Announce the connection target on stderr, redacted.

    Stderr rather than stdout so the line survives a piped or redirected run
    and cannot corrupt a script's real output.
    """
    redacted = redact_dsn(dsn)
    print(f"{role}: {redacted or UNREDACTABLE}", file=stream or sys.stderr)
