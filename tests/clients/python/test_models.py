"""Generated models parse every body the API sends (#618).

``HTTPValidationError`` has two shapes on the wire: FastAPI's list of field
errors, and the single message a route's own ``HTTPException(422, ...)`` sends.
The published schema widens ``detail`` to admit both (``src/api/openapi.py``),
so the generated model must parse both.
"""

from power_map_client.generated.models import HTTPValidationError, ValidationError


def test_422_with_field_errors():
    body = {"detail": [{"loc": ["query", "limit"], "msg": "too big", "type": "less_than"}]}
    parsed = HTTPValidationError.from_dict(body)
    assert isinstance(parsed.detail[0], ValidationError)
    assert parsed.detail[0].msg == "too big"


def test_422_with_a_message():
    body = {"detail": "Embedding dimension 3 does not match model 'm' expected 256"}
    assert HTTPValidationError.from_dict(body).detail == body["detail"]
