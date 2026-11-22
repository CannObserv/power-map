"""Source-level sweep: admin flash *levels* follow one convention per action class (#353).

The admin flash taxonomy (docs/ADMIN.md), agreed in #353 after auditing every ``flash_trigger`` call
and every ``_FLASH_MESSAGES`` / ``SHARED_FLASH_MESSAGES`` registry:

- ``success`` — any mutation that changed state (create / edit / delete /
  archive / unarchive). Level answers "did it succeed"; the **body text**
  ("Name removed." vs "Person deleted.") carries the create-vs-delete meaning.
- ``warning`` — rejected, nothing changed (bad input, uniqueness / 409 conflict,
  business-rule violation).
- ``error``  — unexpected server / operation failure only. None exist today;
  a new one must be added to ``ERROR_ALLOWED`` deliberately.
- ``info``   — retired from mutation confirmations.

Before #353 the same verb flashed different levels across surfaces: an ancillary
delete flashed ``info`` while a Danger Zone delete flashed ``success``; an HTMX
input rejection flashed ``error`` while its own non-HTMX fallback flashed
``warning``. These guards ratchet the two never-again invariants — no ``info``
level, and no ``error`` level outside the allowlist — so the split can't reaccrue.
"""

import ast
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "admin"

# Allow-list keys are "<file>.py::<function>": the innermost def enclosing the
# call (#853). A line-number key broke on every edit above the call. One entry
# covers exactly one site: a second matching call in the same function fails.

# flash_trigger("error", …) sites vetted as genuine unexpected server/operation
# failures (not user-input rejections). Empty by design — every current error
# flash is a rejection and belongs at `warning`.
ERROR_ALLOWED: frozenset[str] = frozenset()

# flash_trigger(<var>, …) sites whose level is a variable rather than a string
# constant. These bypass the level sweeps below (the AST can't resolve the
# value), so each must be vetted by hand and allowlisted here.
# orgs.py active-toggle resolves `level` to success/warning only (#353).
DYNAMIC_LEVEL_ALLOWED: frozenset[str] = frozenset({"orgs.py::org_inline_active_post"})

_MODULE = "<module>"


@dataclass(frozen=True)
class FlashCall:
    """One ``flash_trigger(<level>, …)`` call and the def that encloses it."""

    file: str
    func: str  # innermost enclosing def's name, or "<module>"
    def_line: int  # that def's own line (0 at module level): tells same-named defs apart
    line: int
    level: ast.expr

    @property
    def key(self) -> str:
        """Allow-list key: ``<file>::<function>``."""
        return f"{self.file}::{self.func}"

    @property
    def site(self) -> str:
        """Human locator for failure messages: ``<file>:<line>``."""
        return f"{self.file}:{self.line}"

    @property
    def const_level(self) -> str | None:
        """The level when it is a string constant, else None."""
        if isinstance(self.level, ast.Constant) and isinstance(self.level.value, str):
            return self.level.value
        return None


def _walk_calls(node: ast.AST, file: str, func: str, def_line: int) -> Iterator[FlashCall]:
    """Yield the calls under *node*, which sits in def *func* (at *def_line*)."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        # Decorators, defaults and annotations are evaluated in the enclosing
        # scope; only the body belongs to the def itself.
        outer = [*node.decorator_list, node.args, *([node.returns] if node.returns else [])]
        for part in outer:
            yield from _walk_calls(part, file, func, def_line)
        for stmt in node.body:
            yield from _walk_calls(stmt, file, node.name, node.lineno)
        return
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "flash_trigger"
        and node.args
    ):
        yield FlashCall(file, func, def_line, node.lineno, node.args[0])
    for child in ast.iter_child_nodes(node):
        yield from _walk_calls(child, file, func, def_line)


def _calls_in_source(file: str, source: str) -> list[FlashCall]:
    """Every ``flash_trigger(<level>, …)`` call in *source*, in line order.

    AST-based so both the ``headers=flash_trigger(...)`` and ``**flash_trigger(...)``
    spellings are caught, and a mention in a comment/string is not. The tree is
    walked with the enclosing def in hand (``ast.walk`` loses the parent).
    """
    calls = _walk_calls(ast.parse(source, filename=file), file, _MODULE, 0)
    return sorted(calls, key=lambda c: c.line)


def _flash_trigger_calls() -> list[FlashCall]:
    """Every ``flash_trigger`` call across ``src/api/admin/*.py``."""
    calls: list[FlashCall] = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        calls += _calls_in_source(path.name, path.read_text())
    return calls


def _dynamic_sites(calls: list[FlashCall]) -> list[FlashCall]:
    """Calls whose level is not a string constant (#353's blind spot)."""
    return [c for c in calls if c.const_level is None]


def _error_sites(calls: list[FlashCall]) -> list[FlashCall]:
    """Calls whose level is the constant ``"error"``."""
    return [c for c in calls if c.const_level == "error"]


def _allowlist_violations(sites: list[FlashCall], allowed: frozenset[str]) -> list[str]:
    """Check *sites* (calls that each need an entry) against an allow-list.

    Flags a site whose function isn't listed, a listed function holding more
    than one site, and a listed key that matches no site (stale entry).
    """
    by_key: dict[str, list[FlashCall]] = defaultdict(list)
    for c in sites:
        by_key[c.key].append(c)
    out = [f"{c.site} ({c.key}) is not allow-listed" for c in sites if c.key not in allowed]
    out += [
        f"{key} is allow-listed for one site but holds {len(cs)}: " + ", ".join(c.site for c in cs)
        for key, cs in by_key.items()
        if key in allowed and len(cs) > 1
    ]
    out += [f"{key} is allow-listed but matches no site" for key in sorted(allowed - by_key.keys())]
    return out


def _key_collisions(calls: list[FlashCall]) -> list[str]:
    """Keys reached from more than one def in the same file."""
    defs: dict[str, set[int]] = defaultdict(set)
    for c in calls:
        defs[c.key].add(c.def_line)
    return [
        f"{key} (defs at " + ", ".join(f"L{n}" for n in sorted(lines)) + ")"
        for key, lines in defs.items()
        if len(lines) > 1
    ]


def test_flash_trigger_level_is_constant():
    """Every server-side flash_trigger level is a string constant, else allowlisted (#353).

    The level sweeps below only see constant-level calls; a `flash_trigger(level, …)`
    with a variable level slips past them. This guards the blind spot: a new dynamic
    level must be vetted by hand and its function added to DYNAMIC_LEVEL_ALLOWED, so
    `info`/`error` can't sneak back in through a variable.
    """
    offenders = _allowlist_violations(_dynamic_sites(_flash_trigger_calls()), DYNAMIC_LEVEL_ALLOWED)
    assert not offenders, (
        "flash_trigger called with a non-constant level bypasses the #353 level "
        "sweeps. Vet the resolved values and add the enclosing function to "
        f"DYNAMIC_LEVEL_ALLOWED as '<file>.py::<function>'. Violations: {offenders}"
    )


def test_no_info_flash_level_in_admin_mutations():
    """`info` is retired — every successful mutation flashes `success` (#353)."""
    offenders = [c.site for c in _flash_trigger_calls() if c.const_level == "info"]
    assert not offenders, (
        "flash_trigger('info', …) is retired by #353 — successful deletes flash "
        f"'success' (body text carries the verb). Offending sites: {offenders}"
    )


def test_no_unallowlisted_error_flash_level():
    """`error` is reserved for unexpected server failures — rejections use `warning` (#353)."""
    offenders = _allowlist_violations(_error_sites(_flash_trigger_calls()), ERROR_ALLOWED)
    assert not offenders, (
        "flash_trigger('error', …) is reserved for unexpected server/operation "
        "failures; a user-input rejection (bad input, 409 conflict, business-rule "
        "violation) flashes 'warning' per #353. If a site is a genuine server "
        "failure add its enclosing function to ERROR_ALLOWED as "
        f"'<file>.py::<function>'. Violations: {offenders}"
    )


def _registry_levels(module: str, name: str) -> dict[str, str]:
    """Return {key: level} for a module-level ``<name>`` dict of (level, body) tuples."""
    tree = ast.parse((ADMIN_DIR / module).read_text())
    for node in ast.walk(tree):
        # Handle both `NAME = {...}` (Assign) and `NAME: dict[...] = {...}` (AnnAssign).
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            value = node.value
        else:
            continue
        out: dict[str, str] = {}
        for k, v in zip(value.keys, value.values, strict=True):
            out[k.value] = v.elts[0].value
        return out
    raise AssertionError(f"{name} not found in {module}")


def test_shared_flash_messages_levels_match_taxonomy():
    """`saved`/`removed` → success, `invalid`/`exists` → warning (#353); the overlay's
    pin keys (#498) are mutations that changed state → success, save `pin_stale`,
    `already_pinned` and `slot_changed` (#838), rejected no-ops → warning.
    `saved_unstandardized` (#589) is a save that happened with the address stored
    as typed → success, caveat in the body."""
    assert _registry_levels("deps.py", "SHARED_FLASH_MESSAGES") == {
        "saved": "success",
        "saved_unstandardized": "success",
        "removed": "success",
        "invalid": "warning",
        "exists": "warning",
        "locked": "warning",
        "identifier_locked": "warning",  # #710
        "org_archived": "warning",  # #835
        "saved_pinned": "success",
        "removed_pinned": "success",
        "pinned": "success",
        "unpinned": "success",
        "pin_stale": "warning",
        "already_pinned": "warning",
        "slot_changed": "warning",  # #838
    }


def test_module_flash_registries_are_success():
    """Every per-module `_FLASH_MESSAGES` action (archived/unarchived/deleted) → success (#353)."""
    offenders: list[str] = []
    for module in ("orgs.py", "people.py", "jurisdictions.py", "roles.py", "role_assignments.py"):
        for key, level in _registry_levels(module, "_FLASH_MESSAGES").items():
            if level != "success":
                offenders.append(f"{module}:{key}={level}")
    assert not offenders, f"per-module _FLASH_MESSAGES must be 'success': {offenders}"


# ── #853: allow-lists key on the enclosing function, not the line number ─────


_DYNAMIC_SRC = """
def handler(ok):
    level = "success" if ok else "warning"
    return flash_trigger(level, "x")
"""


def test_call_records_innermost_enclosing_function():
    """Each call is keyed by its nearest def — nested factory handlers included."""
    src = """
flash_trigger("success", "module")

def make_router():
    async def inner():
        return flash_trigger("success", "x")
    return flash_trigger("warning", "y")
"""
    calls = _calls_in_source("m.py", src)
    assert [(c.key, c.line) for c in calls] == [
        ("m.py::<module>", 2),
        ("m.py::inner", 6),
        ("m.py::make_router", 7),
    ]


def test_decorator_and_default_calls_belong_to_the_enclosing_scope():
    """Decorators, defaults and annotations run in the outer scope, not the def's body."""
    src = """
def outer():
    @deco(flash_trigger("success", "d"))
    def inner(h=flash_trigger("success", "x")) -> flash_trigger("success", "r"):
        return flash_trigger("success", "body")
"""
    assert [c.key for c in _calls_in_source("m.py", src)] == [
        "m.py::outer",
        "m.py::outer",
        "m.py::outer",
        "m.py::inner",
    ]


def test_allowlisted_dynamic_site_survives_line_shift_in_real_orgs():
    """Acceptance (#853): lines added above the vetted call don't break the guard."""
    shifted = "\n" * 25 + (ADMIN_DIR / "orgs.py").read_text()
    calls = _calls_in_source("orgs.py", shifted)
    assert _allowlist_violations(_dynamic_sites(calls), DYNAMIC_LEVEL_ALLOWED) == []


def test_unallowlisted_dynamic_site_is_flagged_with_its_line():
    calls = _calls_in_source("m.py", _DYNAMIC_SRC)
    assert _allowlist_violations(_dynamic_sites(calls), frozenset()) == [
        "m.py:4 (m.py::handler) is not allow-listed"
    ]


def test_second_dynamic_site_in_allowlisted_function_is_flagged():
    """Strict: allow-listing a function covers exactly one site in it."""
    src = _DYNAMIC_SRC + '    flash_trigger(level, "again")\n'
    calls = _calls_in_source("m.py", src)
    assert _allowlist_violations(_dynamic_sites(calls), frozenset({"m.py::handler"})) == [
        "m.py::handler is allow-listed for one site but holds 2: m.py:4, m.py:5"
    ]


def test_stale_allowlist_entry_is_flagged():
    calls = _calls_in_source("m.py", _DYNAMIC_SRC)
    assert _allowlist_violations(
        _dynamic_sites(calls), frozenset({"m.py::handler", "m.py::gone"})
    ) == ["m.py::gone is allow-listed but matches no site"]


def test_error_sites_are_matched_by_function_key():
    src = 'def boom():\n    return flash_trigger("error", "x")\n'
    calls = _calls_in_source("m.py", src)
    assert _allowlist_violations(_error_sites(calls), frozenset({"m.py::boom"})) == []
    assert _allowlist_violations(_error_sites(calls), frozenset()) == [
        "m.py:2 (m.py::boom) is not allow-listed"
    ]


def test_same_named_functions_in_one_file_collide():
    src = """
def h():
    return flash_trigger("success", "a")

def h():
    return flash_trigger("success", "b")
"""
    assert _key_collisions(_calls_in_source("m.py", src)) == ["m.py::h (defs at L2, L5)"]


def test_flash_trigger_keys_are_unique_per_file():
    """Two defs sharing a name in one file would share an allow-list key (#853)."""
    collisions = _key_collisions(_flash_trigger_calls())
    assert not collisions, (
        "Two functions in one admin module share a name, so an allow-list entry "
        f"would cover both. Rename one: {collisions}"
    )
