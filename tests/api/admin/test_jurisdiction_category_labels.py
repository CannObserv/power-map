"""Tests for the ``rel_category_label`` Jinja filter wiring (#278).

The filter maps relationship-category slugs to curated display labels via
``src.core.jurisdictions.relationship_category_label``. Registered on every
admin ``Jinja2Templates`` instance by the injector in ``src.api.admin.assets``
(same walk pattern as ``asset_version``), replacing the ``| title`` stopgap
in the jurisdiction relationship partials.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from src.api.admin import assets
from tests.api.jinja_templates_walker import walk_admin_jinja_templates

TEMPLATES = Path("src/templates")
CATEGORY_TEMPLATES = [
    "admin/jurisdictions/partials/_relationship_row.html",
    "admin/jurisdictions/partials/_relationship_form_row.html",
]


def test_register_rel_category_label_filter_renders_curated_label(tmp_path):
    templates = Jinja2Templates(directory=str(tmp_path))
    assets.register_rel_category_label_filter(templates)
    rendered = templates.env.from_string("{{ 'governance' | rel_category_label }}").render()
    assert rendered == "Governance"


def test_every_admin_jinja_env_has_rel_category_label_filter():
    assets.inject_rel_category_label_into_admin_templates()
    instances = walk_admin_jinja_templates()
    assert instances, "expected at least one Jinja2Templates in src.api.admin"
    missing = [name for name, t in instances if "rel_category_label" not in t.env.filters]
    assert not missing, (
        f"rel_category_label filter missing on {len(missing)} Jinja2Templates instance(s): "
        f"{missing}. The injector in src.api.admin.assets probably skipped them — "
        "check whether they live in a subpackage."
    )


def test_category_templates_use_filter_not_title_stopgap():
    """The partials must render categories through rel_category_label, not | title.

    ``| title`` on the raw slug was the #275 stopgap this filter replaces —
    it has no single source of truth and won't generalize (multi-word,
    special casing, i18n).
    """
    for template in CATEGORY_TEMPLATES:
        text = (TEMPLATES / template).read_text()
        assert "rel_category_label" in text, f"{template} does not use rel_category_label"
        normalized = text.replace("|title", "| title")
        assert "| title" not in normalized, f"{template} still uses the | title stopgap"
