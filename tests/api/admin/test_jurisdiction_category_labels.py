"""Tests for the ``category_label`` Jinja filter wiring (#278).

The filter maps relationship-category slugs to curated display labels via
``src.core.jurisdictions.relationship_category_label``. Registered on every
admin ``Jinja2Templates`` instance by the injector in ``src.api.admin.assets``
(same walk pattern as ``asset_version``), replacing the ``| title`` stopgap
in the jurisdiction relationship partials.
"""

import importlib
import pkgutil
from pathlib import Path

from fastapi.templating import Jinja2Templates

import src.api.admin as admin_pkg
from src.api.admin import assets

TEMPLATES = Path("src/templates")
CATEGORY_TEMPLATES = [
    "admin/jurisdictions/partials/_relationship_row.html",
    "admin/jurisdictions/partials/_relationship_form_row.html",
]


def _walk_admin_jinja_templates() -> list[tuple[str, Jinja2Templates]]:
    """Yield every (module_name, Jinja2Templates) reachable in src.api.admin.

    Recurses via ``walk_packages`` (the production injector only walks the
    top level) so a templates instance in a future subpackage surfaces here
    as an injection gap before it ships. Same rationale as the walker in
    ``tests/api/test_assets.py``.
    """
    found: list[tuple[str, Jinja2Templates]] = []
    for mod_info in pkgutil.walk_packages(admin_pkg.__path__, prefix=f"{admin_pkg.__name__}."):
        module = importlib.import_module(mod_info.name)
        for attr_name, attr in module.__dict__.items():
            if isinstance(attr, Jinja2Templates):
                found.append((f"{mod_info.name}.{attr_name}", attr))
    return found


def test_register_category_label_filter_renders_curated_label(tmp_path):
    templates = Jinja2Templates(directory=str(tmp_path))
    assets.register_category_label_filter(templates)
    rendered = templates.env.from_string("{{ 'governance' | category_label }}").render()
    assert rendered == "Governance"


def test_every_admin_jinja_env_has_category_label_filter():
    assets.inject_category_label_into_admin_templates()
    instances = _walk_admin_jinja_templates()
    assert instances, "expected at least one Jinja2Templates in src.api.admin"
    missing = [name for name, t in instances if "category_label" not in t.env.filters]
    assert not missing, (
        f"category_label filter missing on {len(missing)} Jinja2Templates instance(s): "
        f"{missing}. The injector in src.api.admin.assets probably skipped them — "
        "check whether they live in a subpackage."
    )


def test_category_templates_use_filter_not_title_stopgap():
    """The partials must render categories through category_label, not | title.

    ``| title`` on the raw slug was the #275 stopgap this filter replaces —
    it has no single source of truth and won't generalize (multi-word,
    special casing, i18n).
    """
    for template in CATEGORY_TEMPLATES:
        text = (TEMPLATES / template).read_text()
        assert "category_label" in text, f"{template} does not use category_label"
        normalized = text.replace("|title", "| title")
        assert "| title" not in normalized, f"{template} still uses the | title stopgap"
