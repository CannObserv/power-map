from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.relationship_list_response import RelationshipListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pm_assignment_id: str,
    *,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(if_none_match, Unset):
        headers["If-None-Match"] = if_none_match

    params: dict[str, Any] = {}

    params["include_archived"] = include_archived

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assignments/{pm_assignment_id}/relationships".format(
            pm_assignment_id=quote(str(pm_assignment_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorDetail | HTTPValidationError | RelationshipListResponse | None:
    if response.status_code == 200:
        response_200 = RelationshipListResponse.from_dict(response.json())

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
) -> Response[Any | ErrorDetail | HTTPValidationError | RelationshipListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pm_assignment_id: str,
    *,
    client: AuthenticatedClient,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | ErrorDetail | HTTPValidationError | RelationshipListResponse]:
    """List Assignment Relationships

     Return every relationship touching the assignment (either direction), newest first.

    ``include_archived=true`` includes retracted / cascade-archived edges. Stable
    offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on a unique
    column (#297).

    Conditional GET (#392): watermark validator spanning *both* directions, so an
    inbound edge moves the tag too; the cascade (#301) archives edges when an
    endpoint shrinks, which the active-only count catches.

    Args:
        pm_assignment_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorDetail | HTTPValidationError | RelationshipListResponse]
    """

    kwargs = _get_kwargs(
        pm_assignment_id=pm_assignment_id,
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
    pm_assignment_id: str,
    *,
    client: AuthenticatedClient,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Any | ErrorDetail | HTTPValidationError | RelationshipListResponse | None:
    """List Assignment Relationships

     Return every relationship touching the assignment (either direction), newest first.

    ``include_archived=true`` includes retracted / cascade-archived edges. Stable
    offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on a unique
    column (#297).

    Conditional GET (#392): watermark validator spanning *both* directions, so an
    inbound edge moves the tag too; the cascade (#301) archives edges when an
    endpoint shrinks, which the active-only count catches.

    Args:
        pm_assignment_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorDetail | HTTPValidationError | RelationshipListResponse
    """

    return sync_detailed(
        pm_assignment_id=pm_assignment_id,
        client=client,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        if_none_match=if_none_match,
    ).parsed


async def asyncio_detailed(
    pm_assignment_id: str,
    *,
    client: AuthenticatedClient,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | ErrorDetail | HTTPValidationError | RelationshipListResponse]:
    """List Assignment Relationships

     Return every relationship touching the assignment (either direction), newest first.

    ``include_archived=true`` includes retracted / cascade-archived edges. Stable
    offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on a unique
    column (#297).

    Conditional GET (#392): watermark validator spanning *both* directions, so an
    inbound edge moves the tag too; the cascade (#301) archives edges when an
    endpoint shrinks, which the active-only count catches.

    Args:
        pm_assignment_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorDetail | HTTPValidationError | RelationshipListResponse]
    """

    kwargs = _get_kwargs(
        pm_assignment_id=pm_assignment_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
        if_none_match=if_none_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pm_assignment_id: str,
    *,
    client: AuthenticatedClient,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    if_none_match: str | Unset = UNSET,
) -> Any | ErrorDetail | HTTPValidationError | RelationshipListResponse | None:
    """List Assignment Relationships

     Return every relationship touching the assignment (either direction), newest first.

    ``include_archived=true`` includes retracted / cascade-archived edges. Stable
    offset pagination — ``ORDER BY created_at DESC, id DESC`` ends on a unique
    column (#297).

    Conditional GET (#392): watermark validator spanning *both* directions, so an
    inbound edge moves the tag too; the cascade (#301) archives edges when an
    endpoint shrinks, which the active-only count catches.

    Args:
        pm_assignment_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorDetail | HTTPValidationError | RelationshipListResponse
    """

    return (
        await asyncio_detailed(
            pm_assignment_id=pm_assignment_id,
            client=client,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
            if_none_match=if_none_match,
        )
    ).parsed
