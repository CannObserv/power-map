"""Shared connection-target diagnostics for operational scripts (#402).

`DATABASE_URL` comes from `/etc/power-map/.env` and points at **production**,
from any directory — main checkout, worktree, anywhere on the VM. A script that
prints nothing about where it connected leaves no way, during or after the run,
to tell which database it touched.

Every function here is diagnostic: none of them may raise on a caller's behalf.
A DSN that is not a parseable URL is reported as unredactable and **never
echoed** — `urlparse` hands the credentials back as the "path" for a libpq
keyword/value DSN, and printing that would put the password in the journal.

Seeded by #402 for the two scripts it gates. #399 extends this module with
prod/test labelling (keyed on `(host, port, dbname)`, not DSN string equality —
production has two DSNs for one database) and retrofits it across the live
scripts, with an AST sweep to keep it from depending on memory.

`scripts/apply-schema.sh` deliberately keeps its own copy of this logic: it
runs as `ExecStartPre` on the systemd unit, where an import failure would mean
a failed production restart.
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
