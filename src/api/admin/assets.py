"""Admin static-asset cache-bust version resolver.

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
"""

import importlib
import pkgutil
import subprocess
import time

from fastapi.templating import Jinja2Templates

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


def inject_asset_version_into_admin_templates() -> None:
    """Set ``asset_version`` on every Jinja2Templates instance under src.api.admin.

    Walks the immediate ``src.api.admin`` namespace (one level — does NOT
    recurse into subpackages) and registers the version on every module-level
    ``Jinja2Templates`` it finds. Called once at app startup from
    ``src/api/main.py``.

    Iterates ``module.__dict__.values()`` directly rather than via
    ``dir()`` + ``getattr()``: faster and avoids triggering attribute
    descriptors. The src.api.admin import is lazy so this module can be
    imported cheaply (e.g. by tests) without dragging the whole admin
    package into memory at import time.
    """
    import src.api.admin as admin_pkg  # lazy: avoid circular import at module load

    for mod_info in pkgutil.iter_modules(admin_pkg.__path__):
        module = importlib.import_module(f"{admin_pkg.__name__}.{mod_info.name}")
        for attr in module.__dict__.values():
            if isinstance(attr, Jinja2Templates):
                register_asset_version_global(attr)
