"""An address form's ``components`` JSON is depth-bounded (#674, CR 6). Pure unit.

The field arrives inside a form, which the request boundary never parses as
JSON, so each address module's own parse must bound it.
"""

import pytest

from src.api.admin import jurisdictions_addresses, orgs_addresses, people_addresses
from src.core.json_depth import MAX_JSON_DEPTH

_MODULES = [people_addresses, orgs_addresses, jurisdictions_addresses]


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
@pytest.mark.parametrize("depth", [MAX_JSON_DEPTH + 1, 100_000])
def test_too_deep_components_are_invalid(module, depth):
    with pytest.raises(ValueError, match="components must be valid JSON"):
        module._parse_normalizer_fields("1 MAIN ST", "", "", "[" * depth + "]" * depth)


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_components_at_the_bound_are_kept(module):
    comp = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH
    assert module._parse_normalizer_fields("1 MAIN ST", "", "", comp)[3] == comp
