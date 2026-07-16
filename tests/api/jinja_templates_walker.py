"""Shared test walker over admin Jinja2Templates instances.

Used by every test that asserts a startup injector reached all admin
template envs (asset_version, rel_category_label, ...). Recurses via
``walk_packages`` — deliberately wider than the production injectors in
``src.api.admin.assets`` (``iter_modules``, one level only) — so a
templates instance in a future ``src.api.admin.<subpkg>`` surfaces here
as an injection gap before it ships.

Iterates ``module.__dict__.items()`` directly (faster than ``dir()`` +
``getattr()``, and avoids triggering attribute descriptors). Lets
``ImportError`` propagate: a module that fails to import is the worst
case — its templates would receive zero injection — and silently
skipping it would mask the very gap the callers exist to detect.
"""

import importlib
import pkgutil

from fastapi.templating import Jinja2Templates

import src.api.admin as admin_pkg


def walk_admin_jinja_templates() -> list[tuple[str, Jinja2Templates]]:
    """Return every (module_name.attr_name, Jinja2Templates) in src.api.admin."""
    found: list[tuple[str, Jinja2Templates]] = []
    for mod_info in pkgutil.walk_packages(admin_pkg.__path__, prefix=f"{admin_pkg.__name__}."):
        module = importlib.import_module(mod_info.name)
        for attr_name, attr in module.__dict__.items():
            if isinstance(attr, Jinja2Templates):
                found.append((f"{mod_info.name}.{attr_name}", attr))
    return found
