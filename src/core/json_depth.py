"""JSON nested too deep to walk recursively (#674).

``json.loads`` parses to a depth of about 10,000, but every recursive walk over
the result — ``jsonable_encoder``, ``find_unstorable``, ``escape_unstorable_deep``
— hits Python's recursion limit near 1,000. One bound, ``MAX_JSON_DEPTH``, is
shared by every place that walks a decoded body: the API boundary rejects a
body past it, the 422 handler will not echo an ``input`` past it, and the
request log stores such a body unparsed. Each checks with ``json_depth_exceeds``,
which is iterative, so the check itself is safe at any depth.

Depth counts nested containers: ``"ok"`` is 0, ``[]`` is 1, ``{"a": []}`` is 2.
"""

from typing import Any

MAX_JSON_DEPTH = 64

_CONTAINERS = (dict, list, tuple)


def json_depth_exceeds(obj: Any, limit: int = MAX_JSON_DEPTH) -> bool:
    """True if ``obj`` nests containers deeper than ``limit``. Never recurses."""
    stack = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, _CONTAINERS):
            continue
        depth += 1
        if depth > limit:
            return True
        children = node.values() if isinstance(node, dict) else node
        stack.extend((child, depth) for child in children if isinstance(child, _CONTAINERS))
    return False


def json_may_be_too_deep(raw: bytes) -> bool:
    """Cheap pre-screen: False guarantees the JSON body nests no deeper than the bound.

    Every container opens with ``[`` or ``{``, so a body needs more than
    ``MAX_JSON_DEPTH`` of them to exceed it. Brackets inside strings count too,
    so a hit is only a candidate, confirmed by ``json_depth_exceeds``.
    """
    return raw.count(b"[") + raw.count(b"{") > MAX_JSON_DEPTH
