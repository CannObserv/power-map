"""The published OpenAPI schema: FastAPI's, with two corrections (#618).

``/openapi.json`` is the contract ``clients/python`` is generated from, so a
statement it gets wrong becomes a wrong type in every consumer. FastAPI derives
the schema from the routes and gets two things wrong here. This module corrects
both, in one place:

- **The 422 body.** FastAPI declares ``HTTPValidationError`` as
  ``{detail: [ValidationError]}``. Routes that reject a request themselves raise
  ``HTTPException(422, detail="...")``: an embedding of the wrong dimension, an
  unknown identifier type. That ``detail`` is a string. The published model
  admits both forms, so a generated client parses either.
- **``If-None-Match``.** Conditional GETs (#292/#392) read the header inside
  ``conditional_response``, never as a route parameter, so FastAPI does not
  publish it. Every operation that declares ``304`` gets the optional header
  parameter that earns the ``304``, so a generated client takes it as an
  argument. It is derived from the 304 declaration and cannot drift from it.

The result is cached on ``app.openapi_schema`` exactly as FastAPI caches its own.
"""

import copy
from typing import Any

from fastapi import FastAPI

_IF_NONE_MATCH: dict[str, Any] = {
    "name": "If-None-Match",
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
    "description": "An ETag from an earlier response. When it still matches, "
    "the answer is 304 with no body.",
}


def _widen_validation_detail(schema: dict[str, Any]) -> None:
    """Let ``HTTPValidationError.detail`` be the list FastAPI emits or a string."""
    body = schema.get("components", {}).get("schemas", {}).get("HTTPValidationError")
    if body is None:
        return
    as_list = body["properties"]["detail"]
    body["properties"]["detail"] = {
        "title": as_list.get("title", "Detail"),
        "anyOf": [
            {"type": "array", "items": as_list["items"]},
            {"type": "string"},
        ],
        "description": "Field errors from request validation, or one message "
        "when the route rejects the request itself.",
    }


def _add_if_none_match(schema: dict[str, Any]) -> None:
    """Publish the optional ``If-None-Match`` header on every 304-capable operation."""
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if "304" not in operation.get("responses", {}):
                continue
            params = operation.setdefault("parameters", [])
            declared = {(p.get("in"), p.get("name")) for p in params}
            if ("header", "If-None-Match") not in declared:
                params.append(copy.deepcopy(_IF_NONE_MATCH))


def install_public_openapi(app: FastAPI) -> None:
    """Replace ``app.openapi`` with FastAPI's builder plus the corrections above."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = FastAPI.openapi(app)  # FastAPI's build; caches on app.openapi_schema
            _widen_validation_detail(schema)
            _add_if_none_match(schema)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
