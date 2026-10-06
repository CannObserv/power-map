from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.entity_gone import EntityGone
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_response import JurisdictionResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    jurisdiction_id: str,
    *,
    if_none_match: str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(if_none_match, Unset):
        headers["If-None-Match"] = if_none_match

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions/{jurisdiction_id}".format(
            jurisdiction_id=quote(str(jurisdiction_id), safe=""),
        ),
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse | None:
    if response.status_code == 200:
        response_200 = JurisdictionResponse.from_dict(response.json())

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

    if response.status_code == 404:
        response_404 = ErrorDetail.from_dict(response.json())

        return response_404

    if response.status_code == 410:
        response_410 = EntityGone.from_dict(response.json())

        return response_410

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
) -> Response[Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse]:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        if_none_match=if_none_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    if_none_match: str | Unset = UNSET,
) -> Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse | None:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse
    """

    return sync_detailed(
        jurisdiction_id=jurisdiction_id,
        client=client,
        if_none_match=if_none_match,
    ).parsed


async def asyncio_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    if_none_match: str | Unset = UNSET,
) -> Response[Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse]:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        if_none_match=if_none_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    if_none_match: str | Unset = UNSET,
) -> Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse | None:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):
        if_none_match (str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EntityGone | ErrorDetail | HTTPValidationError | JurisdictionResponse
    """

    return (
        await asyncio_detailed(
            jurisdiction_id=jurisdiction_id,
            client=client,
            if_none_match=if_none_match,
        )
    ).parsed
