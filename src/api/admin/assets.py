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
"""

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
