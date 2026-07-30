"""Source-level sweep: every admin mutation route carries a non-HTMX fallback (#349).

§32 convention: mutation handlers branch on ``is_htmx(request)`` and return a
``RedirectResponse`` (or ``HX-Location`` 204) fallback for non-HTMX clients.
This guard parses every ``src/api/admin/*.py`` module and flags any
POST/PUT/DELETE/PATCH-decorated handler whose body carries none of the fallback
markers, so a new mutation route without the branch fails CI instead of
accumulating (the drift that produced the 12 handlers fixed in #349).

The check is heuristic (marker strings anywhere in the handler body), which is
the right cost/benefit for a ratchet: it counts factory-made handlers once at
the source level and cannot false-negative on a handler that genuinely lacks
the branch. Vetted exceptions go in ALLOWED with a reason comment.
"""

import ast
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "admin"

_MUTATION_METHODS = {"post", "put", "delete", "patch"}

# A handler satisfies the convention when its body mentions any of these:
# - is_htmx / RedirectResponse: the standard §32 branch
# - HX-Location: the archive-style variant (204 + HX-Location for HTMX,
#   RedirectResponse otherwise) where the redirect happens client-side
_FALLBACK_MARKERS = ("is_htmx", "RedirectResponse", "HX-Location")

# "<file>.py::<handler>" entries exempt from the sweep. Each needs a reason.
ALLOWED: frozenset[str] = frozenset()

# "<file>.py::<handler>::L<line>" RedirectResponse sites exempt from the
# flash-carrying ratchet (#351). Each needs a reason.
FLASH_ALLOWED: frozenset[str] = frozenset()


def _mutation_handlers_without_fallback() -> list[str]:
    offenders = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            is_mutation = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _MUTATION_METHODS
                for dec in node.decorator_list
            )
            if not is_mutation:
                continue
            # Scan only the handler body — a marker in a decorator argument
            # must not satisfy the guard (CR #350 finding 2).
            body_src = "\n".join(ast.unparse(stmt) for stmt in node.body)
            if any(marker in body_src for marker in _FALLBACK_MARKERS):
                continue
            offenders.append(f"{path.name}::{node.name}")
    return [o for o in offenders if o not in ALLOWED]


def _is_redirect_response_call(node: ast.AST) -> bool:
    """True for a ``RedirectResponse(...)`` call (bare name or attribute access)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "RedirectResponse"
    if isinstance(func, ast.Attribute):
        return func.attr == "RedirectResponse"
    return False


def _redirect_fallbacks_without_flash() -> list[str]:
    """Every §32 non-HTMX ``RedirectResponse`` fallback must carry a ``?flash=`` (#351).

    A confirmation flash on a non-HTMX create/edit/delete requires the fallback
    redirect to append a flash key — via ``with_flash(url, key)`` or a literal
    ``?flash=`` in the URL. This flags any ``RedirectResponse`` call in a mutation
    handler whose target carries neither, so a new flash-less fallback fails CI.

    A flash carried through a local variable counts: the Danger Zone archive /
    unarchive / delete handlers assign ``target = f"...?flash=archived"`` and
    ``return RedirectResponse(target, ...)`` — the flash is real, just one line up,
    so we resolve single-name arguments against the handler's flash-bearing
    assignments before flagging.
    """
    offenders = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            is_mutation = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _MUTATION_METHODS
                for dec in node.decorator_list
            )
            if not is_mutation:
                continue
            # Names bound to a value whose source mentions ``flash`` — a redirect
            # to one of these carries the flash indirectly.
            flash_vars = set()
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign) and "flash" in ast.unparse(stmt.value):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            flash_vars.add(tgt.id)
            for call in ast.walk(node):
                if not _is_redirect_response_call(call):
                    continue
                if "flash" in ast.unparse(call):
                    continue
                arg0 = call.args[0] if call.args else None
                if isinstance(arg0, ast.Name) and arg0.id in flash_vars:
                    continue
                offenders.append(f"{path.name}::{node.name}::L{call.lineno}")
    return [o for o in offenders if o not in FLASH_ALLOWED]


def test_every_admin_mutation_route_has_nonhtmx_fallback():
    offenders = _mutation_handlers_without_fallback()
    assert offenders == [], (
        "Admin mutation routes missing the §32 non-HTMX fallback branch"
        " (is_htmx → RedirectResponse, or 204 + HX-Location):"
        f" {offenders}. Add the branch, or allowlist with a reason."
    )


def test_every_nonhtmx_fallback_redirect_carries_flash():
    offenders = _redirect_fallbacks_without_flash()
    assert offenders == [], (
        "§32 non-HTMX fallback redirects missing a ?flash= confirmation key (#351)."
        " Wrap the URL with with_flash(url, key) — saved/removed/invalid/exists —"
        f" or add a literal ?flash=: {offenders}"
    )


def test_sweep_sees_admin_mutation_routes():
    """Self-check: the sweep's decorator matching actually finds routes."""
    count = 0
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _MUTATION_METHODS
                for dec in node.decorator_list
            ):
                count += 1
    assert count > 50, f"sweep found only {count} mutation routes — matcher broken?"
