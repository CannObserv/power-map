"""Query and body params flattened over a route's dependency tree (#772).

FastAPI 0.162 removed ``fastapi.dependencies.utils.get_flat_dependant``. Its
replacement, ``get_flat_params``, merges the path, query, header and cookie
params into one list, and the body flattener is private. This walks the
``Dependant`` fields in the same order the 0.135 function did: the route's own
params first, then each sub-dependency's, depth first, keeping repeats.

Not a test module: the two ``test_unstorable_text`` sweeps import it.
"""

from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import ModelField


def _flat(dependant: Dependant, kind: str) -> list[ModelField]:
    fields = list(getattr(dependant, kind))
    for sub in dependant.dependencies:
        fields += _flat(sub, kind)
    return fields


def flat_query_params(dependant: Dependant) -> list[ModelField]:
    """Every query param the route and its dependencies declare."""
    return _flat(dependant, "query_params")


def flat_body_params(dependant: Dependant) -> list[ModelField]:
    """Every body param (JSON field or form field) the route and its dependencies declare."""
    return _flat(dependant, "body_params")
