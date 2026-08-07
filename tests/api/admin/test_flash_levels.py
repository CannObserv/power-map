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
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parents[3] / "src" / "api" / "admin"

# "<file>.py::L<line>" flash_trigger("error", …) sites vetted as genuine
# unexpected server/operation failures (not user-input rejections). Empty by
# design — every current error flash is a rejection and belongs at `warning`.
ERROR_ALLOWED: frozenset[str] = frozenset()

# "<file>.py::L<line>" flash_trigger(<var>, …) sites whose level is a variable
# rather than a string constant. These bypass the level sweeps below (the AST
# can't resolve the value), so each must be vetted by hand and allowlisted here.
# orgs.py active-toggle resolves `level` to success/warning only (#353).
DYNAMIC_LEVEL_ALLOWED: frozenset[str] = frozenset({"orgs.py::L237"})


def _flash_trigger_calls() -> list[tuple[str, int, ast.expr]]:
    """Every ``flash_trigger(<level>, …)`` call: (filename, lineno, first-arg AST node)."""
    calls: list[tuple[str, int, ast.expr]] = []
    for path in sorted(ADMIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "flash_trigger"
                and node.args
            ):
                calls.append((path.name, node.lineno, node.args[0]))
    return calls


def _flash_trigger_level_sites() -> list[tuple[str, int, str]]:
    """Every ``flash_trigger(<const-level>, …)`` call: (filename, lineno, level).

    AST-based so both the ``headers=flash_trigger(...)`` and ``**flash_trigger(...)``
    spellings are caught, and a mention in a comment/string is not. Dynamic-level
    calls (variable first arg) are excluded here — they can't be resolved and are
    covered separately by ``test_flash_trigger_level_is_constant``.
    """
    return [
        (name, line, arg.value)
        for name, line, arg in _flash_trigger_calls()
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]


def test_flash_trigger_level_is_constant():
    """Every server-side flash_trigger level is a string constant, else allowlisted (#353).

    The level sweeps below only see constant-level calls; a `flash_trigger(level, …)`
    with a variable level slips past them. This guards the blind spot: a new dynamic
    level must be vetted by hand and added to DYNAMIC_LEVEL_ALLOWED, so `info`/`error`
    can't sneak back in through a variable.
    """
    offenders = [
        f"{name}:{line}"
        for name, line, arg in _flash_trigger_calls()
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str))
        and f"{name}::L{line}" not in DYNAMIC_LEVEL_ALLOWED
    ]
    assert not offenders, (
        "flash_trigger called with a non-constant level bypasses the #353 level "
        "sweeps. Vet the resolved values and add the site to DYNAMIC_LEVEL_ALLOWED. "
        f"Offending sites: {offenders}"
    )


def test_no_info_flash_level_in_admin_mutations():
    """`info` is retired — every successful mutation flashes `success` (#353)."""
    offenders = [
        f"{name}:{line}" for name, line, level in _flash_trigger_level_sites() if level == "info"
    ]
    assert not offenders, (
        "flash_trigger('info', …) is retired by #353 — successful deletes flash "
        f"'success' (body text carries the verb). Offending sites: {offenders}"
    )


def test_no_unallowlisted_error_flash_level():
    """`error` is reserved for unexpected server failures — rejections use `warning` (#353)."""
    offenders = [
        f"{name}:{line}"
        for name, line, level in _flash_trigger_level_sites()
        if level == "error" and f"{name}::L{line}" not in ERROR_ALLOWED
    ]
    assert not offenders, (
        "flash_trigger('error', …) is reserved for unexpected server/operation "
        "failures; a user-input rejection (bad input, 409 conflict, business-rule "
        "violation) flashes 'warning' per #353. If a site is a genuine server "
        f"failure add it to ERROR_ALLOWED. Offending sites: {offenders}"
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
    """`saved`/`removed` → success, `invalid`/`exists` → warning (#353)."""
    assert _registry_levels("deps.py", "SHARED_FLASH_MESSAGES") == {
        "saved": "success",
        "removed": "success",
        "invalid": "warning",
        "exists": "warning",
    }


def test_module_flash_registries_are_success():
    """Every per-module `_FLASH_MESSAGES` action (archived/unarchived/deleted) → success (#353)."""
    offenders: list[str] = []
    for module in ("orgs.py", "people.py", "jurisdictions.py", "roles.py", "role_assignments.py"):
        for key, level in _registry_levels(module, "_FLASH_MESSAGES").items():
            if level != "success":
                offenders.append(f"{module}:{key}={level}")
    assert not offenders, f"per-module _FLASH_MESSAGES must be 'success': {offenders}"
