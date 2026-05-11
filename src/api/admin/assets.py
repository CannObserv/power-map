"""Admin static-asset cache-bust version resolver + Jinja env-global injectors.

Computes a version string used to bust browser caches for admin JS/CSS. The
string is injected as the Jinja global ``asset_version`` so templates can write
``href="/static/admin/admin.css?v={{ asset_version }}"``.

Resolution order:
1. ``git rev-parse --short HEAD`` — short commit SHA when run from a checkout.
2. Unix-epoch seconds fallback — for container builds and dev contexts that
   lack git or a working tree.

The value is computed once at import time and cached in ``ASSET_VERSION``.

Caveat: the SHA reflects the **git checkout**, not a hash of the static-asset
bytes themselves. For the current single-VM, in-place checkout-and-restart
deploy model this is equivalent. In a deploy where the running process and
the git tree could diverge (e.g. a release artifact bind-mounted alongside an
unrelated checkout, or the process kept alive across ``git pull`` without a
restart), the SHA would no longer match what's actually being served — switch
to hashing the asset files directly if that model becomes relevant.

The fallback timestamp is the **process-start** epoch, not an asset content
hash either: it cache-busts on every restart even when asset bytes are
unchanged. That's intentional — restart-time freshness in dev contexts where
git is unavailable beats stale-cache surprises.

This module also hosts ``inject_array_cap_into_admin_templates``: a sibling
injector that exposes the Python ``ARRAY_CAP`` constant (source of truth in
``src.api.admin.people_name_parts``) as a Jinja env global so the parts editor
template can render ``data-cardstack-cap="{{ ARRAY_CAP }}"`` without
hardcoding the cap. Same walk pattern as ``asset_version``; same startup
call-site in ``src.api.main``.
"""

import importlib
import pkgutil
import subprocess
import time

from fastapi.templating import Jinja2Templates

from src.api.admin.people_name_parts import ARRAY_CAP
from src.core.logging import get_logger

logger = get_logger(__name__)


def compute_asset_version() -> str:
    """Return a short cache-bust version string for admin static assets."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("ascii", errors="replace").strip()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        logger.info(
            "asset_version git lookup failed; falling back to timestamp",
            extra={"error": str(exc)},
        )
        return str(int(time.time()))


ASSET_VERSION: str = compute_asset_version()


def register_asset_version_global(
    templates: Jinja2Templates, version: str = ASSET_VERSION
) -> None:
    """Inject ``asset_version`` into a Jinja2Templates instance's globals."""
    templates.env.globals["asset_version"] = version


def register_array_cap_global(
    templates: Jinja2Templates, cap: int = ARRAY_CAP
) -> None:
    """Inject ``ARRAY_CAP`` into a Jinja2Templates instance's globals."""
    templates.env.globals["ARRAY_CAP"] = cap


def _walk_admin_jinja_templates():
    """Yield every module-level ``Jinja2Templates`` in ``src.api.admin``.

    Same one-level scan as ``iter_modules`` — no recursion into
    subpackages. Iterates ``module.__dict__.values()`` directly (faster
    than ``dir()`` + ``getattr()`` and avoids triggering descriptors).
    The ``src.api.admin`` import is lazy so this module stays cheap to
    import (e.g. for tests that don't need the whole admin package).
    """
    import src.api.admin as admin_pkg  # lazy: avoid circular import at module load

    for mod_info in pkgutil.iter_modules(admin_pkg.__path__):
        module = importlib.import_module(f"{admin_pkg.__name__}.{mod_info.name}")
        for attr in module.__dict__.values():
            if isinstance(attr, Jinja2Templates):
                yield attr


def inject_asset_version_into_admin_templates() -> None:
    """Set ``asset_version`` on every Jinja2Templates instance under src.api.admin.

    Walks the immediate ``src.api.admin`` namespace (one level — does NOT
    recurse into subpackages) and registers the version on every module-level
    ``Jinja2Templates`` it finds. Called once at app startup from
    ``src/api/main.py``.
    """
    for templates in _walk_admin_jinja_templates():
        register_asset_version_global(templates)


def inject_array_cap_into_admin_templates() -> None:
    """Set ``ARRAY_CAP`` on every Jinja2Templates instance under src.api.admin.

    Mirrors the asset_version injector so the parts editor partial can
    render ``data-cardstack-cap="{{ ARRAY_CAP }}"`` regardless of which
    router renders it. Called once at app startup from ``src/api/main.py``.
    """
    for templates in _walk_admin_jinja_templates():
        register_array_cap_global(templates)
