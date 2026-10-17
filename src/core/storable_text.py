"""Text Postgres cannot store: NUL and lone surrogates (#663).

Postgres ``text`` has no NUL (U+0000), and asyncpg encodes every argument as
UTF-8, which has no surrogate code points (U+D800–U+DFFF). Python's ``json``
folds a well-formed surrogate pair into one astral code point, so any code unit
left in that range is unpaired and cannot be encoded. Nothing else is in scope:
other C0 controls are storable, and rejecting them would be a policy change.

The API boundary rejects such text (``src.api.text_boundary``); the 422 handler
and the request log escape it into the literal ``\\u0000`` form so the error
renders and the log row lands.
"""

import re
from typing import Any

_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")

# Byte-level pre-screen for a JSON body (#663): a JSON string can carry NUL or a
# lone surrogate only as a ``\u0000`` / ``\uD800``–``\uDFFF`` escape, a raw NUL
# byte, or the raw bytes ``ED A0``–``ED BF`` (``json.loads`` decodes bytes with
# ``surrogatepass``). A match is only a candidate — ``"\\u0000"`` is a literal
# backslash — so a hit is confirmed by a full parse.
_JSON_CANDIDATE = re.compile(rb"\\u(?:0000|[dD][89a-fA-F][0-9a-fA-F]{2})|\x00|\xed[\xa0-\xbf]")


def has_unstorable(text: str) -> bool:
    """True if ``text`` holds a character Postgres cannot store."""
    return _UNSTORABLE.search(text) is not None


def escape_unstorable(text: str) -> str:
    """Replace each unstorable character with its literal ``\\uXXXX`` escape."""
    return _UNSTORABLE.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


def find_unstorable(obj: Any, loc: tuple = ()) -> tuple[tuple, str] | None:
    """``(path, text)`` for the first unstorable string in a decoded JSON value, else None.

    A dict key is text too: when the key itself is unstorable, the path ends in
    that key, escaped so the path can be rendered, and ``text`` is the key.
    """
    if isinstance(obj, str):
        return (loc, obj) if has_unstorable(obj) else None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and has_unstorable(key):
                return (*loc, escape_unstorable(key)), key
            found = find_unstorable(value, (*loc, key))
            if found is not None:
                return found
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found = find_unstorable(value, (*loc, i))
            if found is not None:
                return found
    return None


def escape_unstorable_deep(obj: Any) -> Any:
    """``obj`` with every string, dict key included, passed through ``escape_unstorable``."""
    if isinstance(obj, str):
        return escape_unstorable(obj)
    if isinstance(obj, dict):
        return {escape_unstorable_deep(k): escape_unstorable_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [escape_unstorable_deep(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(escape_unstorable_deep(v) for v in obj)
    return obj


def json_may_hold_unstorable(raw: bytes) -> bool:
    """Cheap pre-screen: False guarantees the JSON body decodes to storable text."""
    return _JSON_CANDIDATE.search(raw) is not None
