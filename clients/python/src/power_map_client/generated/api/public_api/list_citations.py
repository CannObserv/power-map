from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.citation_list_response import CitationListResponse
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    entity_type: str,
    entity_id: str,
    *,
    field_name: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(if_none_match, Unset):
        headers["If-None-Match"] = if_none_match

    params: dict[str, Any] = {}

    json_field_name: None | str | Unset
    if isinstance(field_name, Unset):
        json_field_name = UNSET
    else:
        json_field_name = field_name
    params["field_name"] = json_field_name

    params["include_archived"] = include_archived

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/citations/{entity_type}/{entity_id}".format(
            entity_type=quote(str(entity_type), safe=""),
            entity_id=quote(str(entity_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | CitationListResponse | ErrorDetail | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CitationListResponse.from_dict(response.json())

        return response_200

    if response.status_code == 304:
        response_304 = cast(Any, None)
        return response_304

    if response.status_code == 401:
        response_401 = ErrorDetail.from_dict(response.json())

        return response_401

    if response.status_code == 403:
        response_403 = ErrorDetail.from_dict(response.json())

        return response_403

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorDetail.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | CitationListResponse | ErrorDetail | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    field_name: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | CitationListResponse | ErrorDetail | HTTPValidationError]:
    """List Citations

     Return citations for an entity, newest first.

    Optional ``field_name`` narrows to a single field's citations (omit for all,
    including whole-entity citations). ``include_archived=true`` includes retracted
    rows. Stable offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on
    a unique column (#297).

    Conditional GET (#392): watermark validator over the *same* filter the body
    uses — a retract archives rather than deletes, so only a count taken over
    the active-only set moves when the default view loses a row.

    Args:
        entity_type (str):
        entity_id (str):
        field_name (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | CitationListResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        if_none_match=if_none_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    field_name: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Any | CitationListResponse | ErrorDetail | HTTPValidationError | None:
    """List Citations

     Return citations for an entity, newest first.

    Optional ``field_name`` narrows to a single field's citations (omit for all,
    including whole-entity citations). ``include_archived=true`` includes retracted
    rows. Stable offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on
    a unique column (#297).

    Conditional GET (#392): watermark validator over the *same* filter the body
    uses — a retract archives rather than deletes, so only a count taken over
    the active-only set moves when the default view loses a row.

    Args:
        entity_type (str):
        entity_id (str):
        field_name (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | CitationListResponse | ErrorDetail | HTTPValidationError
    """

    return sync_detailed(
        entity_type=entity_type,
        entity_id=entity_id,
        client=client,
        field_name=field_name,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        if_none_match=if_none_match,
    ).parsed


async def asyncio_detailed(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    field_name: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | CitationListResponse | ErrorDetail | HTTPValidationError]:
    """List Citations

     Return citations for an entity, newest first.

    Optional ``field_name`` narrows to a single field's citations (omit for all,
    including whole-entity citations). ``include_archived=true`` includes retracted
    rows. Stable offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on
    a unique column (#297).

    Conditional GET (#392): watermark validator over the *same* filter the body
    uses — a retract archives rather than deletes, so only a count taken over
    the active-only set moves when the default view loses a row.

    Args:
        entity_type (str):
        entity_id (str):
        field_name (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | CitationListResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        if_none_match=if_none_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    field_name: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Any | CitationListResponse | ErrorDetail | HTTPValidationError | None:
    """List Citations

     Return citations for an entity, newest first.

    Optional ``field_name`` narrows to a single field's citations (omit for all,
    including whole-entity citations). ``include_archived=true`` includes retracted
    rows. Stable offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on
    a unique column (#297).

    Conditional GET (#392): watermark validator over the *same* filter the body
    uses — a retract archives rather than deletes, so only a count taken over
    the active-only set moves when the default view loses a row.

    Args:
        entity_type (str):
        entity_id (str):
        field_name (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | CitationListResponse | ErrorDetail | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            entity_type=entity_type,
            entity_id=entity_id,
            client=client,
            field_name=field_name,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
            if_none_match=if_none_match,
        )
    ).parsed
