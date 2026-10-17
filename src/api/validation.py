"""The request-validation boundary: the 422 body, and text Postgres cannot store.

**The 422 body** is FastAPI's ``{detail: [ValidationError]}``, made renderable:
Starlette renders JSON with ``allow_nan=False`` and as UTF-8, so an echoed
``input`` holding NaN/Infinity (#299) or a lone surrogate (#663) would crash the
error response into a 500. Both are rewritten before rendering.

**Unstorable text (#663).** NUL and lone surrogates (``src.core.storable_text``)
never reach the database. ``reject_unstorable_text`` is installed once, as an
app-wide dependency, so it runs on every route ahead of the route's own
dependencies (auth, ``get_db``). It checks the path params, every query
param, and a JSON or form body. FastAPI has already read and cached the body
before it solves dependencies, so the check reads that copy and never buffers
more. A JSON body is walked only when a byte-level pre-screen finds a candidate.
A hit answers 422 with ``type: "string_unstorable"`` and a ``loc`` naming the
field.

The admin is an HTMX app, so an HTMX admin request gets the admin's form-error
shape instead (``_names_shared._form_error_response``): 200, a warning flash,
and ``HX-Reswap: none``.

**Backstop.** If the database still refuses text it cannot store
(``CharacterNotInRepertoireError``, or asyncpg's ``DataError`` raised while
encoding an argument), the rule missed a surface. The request still gets the
same 422, and the miss is logged at ERROR.
"""

import email.message
import math

import asyncpg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

from src.api.admin.deps import flash_trigger, is_htmx
from src.core.logging import get_logger
from src.core.storable_text import (
    escape_unstorable,
    escape_unstorable_deep,
    find_unstorable,
    has_unstorable,
    json_may_hold_unstorable,
)

logger = get_logger(__name__)

UNSTORABLE_TYPE = "string_unstorable"
UNSTORABLE_MSG = (
    "Text contains a character that cannot be stored: "
    "NUL (U+0000) or an unpaired surrogate (U+D800-U+DFFF)"
)
_ADMIN_FLASH = "That text contains a character that cannot be stored (NUL). Remove it and retry."
_FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class UnstorableTextError(RequestValidationError):
    """A request input holds text Postgres cannot store (#663)."""

    def __init__(self, loc: tuple, value: object) -> None:
        super().__init__(
            [{"type": UNSTORABLE_TYPE, "loc": loc, "msg": UNSTORABLE_MSG, "input": value}]
        )


def _sanitize_nonfinite(obj: object) -> object:
    """Replace non-finite floats with their string form for JSON encoding (#299)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, list):
        return [_sanitize_nonfinite(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_nonfinite(v) for k, v in obj.items()}
    return obj


def validation_error_response(exc: RequestValidationError) -> JSONResponse:
    """The standard 422, with every echoed value made JSON- and UTF-8-safe."""
    detail = escape_unstorable_deep(_sanitize_nonfinite(jsonable_encoder(exc.errors())))
    return JSONResponse(status_code=422, content={"detail": detail})


def _is_json(content_type: str) -> bool:
    """FastAPI's own JSON test (``fastapi.routing``): ``application/json`` or ``*+json``."""
    message = email.message.Message()
    message["content-type"] = content_type
    subtype = message.get_content_subtype()
    return message.get_content_maintype() == "application" and (
        subtype == "json" or subtype.endswith("+json")
    )


async def _check_body(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    if _is_json(content_type):
        if not json_may_hold_unstorable(await request.body()):
            return
        try:
            data = await request.json()  # FastAPI's cached parse when the route has a body
        except ValueError:
            return  # not JSON: FastAPI answers that itself, or the route never reads it
        found = find_unstorable(data)
        if found is not None:
            loc, text = found
            raise UnstorableTextError(("body", *loc), text)
    elif content_type.startswith(_FORM_TYPES):
        form = await request.form()  # FastAPI's cached parse when the route has form fields
        for name, value in form.multi_items():
            if has_unstorable(name):
                raise UnstorableTextError(("body", escape_unstorable(name)), name)
            if isinstance(value, str) and has_unstorable(value):
                raise UnstorableTextError(("body", name), value)


async def reject_unstorable_text(request: Request) -> None:
    """Reject a request whose path, query or body holds unstorable text (#663)."""
    for name, value in request.path_params.items():
        if isinstance(value, str) and has_unstorable(value):
            raise UnstorableTextError(("path", name), value)
    for name, value in request.query_params.multi_items():
        if has_unstorable(name):
            raise UnstorableTextError(("query", escape_unstorable(name)), name)
        if has_unstorable(value):
            raise UnstorableTextError(("query", name), value)
    if request.method not in ("GET", "HEAD"):
        await _check_body(request)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    return validation_error_response(exc)


async def _unstorable_text_handler(request: Request, exc: UnstorableTextError) -> Response:
    """The standard 422, or the admin's warning flash for an HTMX admin request."""
    if request.url.path.startswith("/admin") and is_htmx(request):
        return HTMLResponse(
            "", headers={**flash_trigger("warning", _ADMIN_FLASH), "HX-Reswap": "none"}
        )
    return validation_error_response(exc)


async def unstorable_text_backstop(request: Request, exc: asyncpg.exceptions.DataError) -> Response:
    """Answer the database's own refusal of unstorable text as the rule would.

    Only the two refusals measured in #663 qualify; any other ``DataError`` is
    re-raised untouched and stays a 500.
    """
    unstorable = isinstance(exc, asyncpg.exceptions.CharacterNotInRepertoireError) or isinstance(
        exc.__cause__, UnicodeEncodeError
    )
    if not unstorable:
        raise exc
    logger.error(
        "unstorable text reached the database: the #663 boundary rule missed a surface "
        "on %s %s (%s)",
        request.method,
        escape_unstorable(request.url.path),
        type(exc).__name__,
    )
    return await _unstorable_text_handler(request, UnstorableTextError((), None))


def install_validation_handlers(app: FastAPI) -> None:
    """Register the 422 handlers and the database backstop on ``app``."""
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(UnstorableTextError, _unstorable_text_handler)
    app.add_exception_handler(asyncpg.exceptions.DataError, unstorable_text_backstop)
