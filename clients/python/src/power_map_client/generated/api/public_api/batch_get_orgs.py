from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.org_batch_get_request import OrgBatchGetRequest
from ...models.org_batch_get_response import OrgBatchGetResponse
from ...types import Response


def _get_kwargs(
    *,
    body: OrgBatchGetRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/orgs/batch-get",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorDetail | HTTPValidationError | OrgBatchGetResponse | None:
    if response.status_code == 200:
        response_200 = OrgBatchGetResponse.from_dict(response.json())

        return response_200

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
) -> Response[ErrorDetail | HTTPValidationError | OrgBatchGetResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: OrgBatchGetRequest,
) -> Response[ErrorDetail | HTTPValidationError | OrgBatchGetResponse]:
    """Batch Get Orgs

     Read many orgs in one call: one ``getOrg`` answer per distinct id (#654).

    Each result is the outcome ``getOrg`` would give that id: ``ok`` (200, the
    same body and ETag), ``not_modified`` (304 against ``etags[id]``),
    ``merged`` / ``gone`` (410 with or without ``merged_into``), ``not_found``
    (404). One bad id never fails the batch; text Postgres cannot store fails
    the whole call with 422 at the boundary (#663). Results come in request order,
    duplicates dropped after their first occurrence. Set-based: the query count
    does not grow with the number of ids, except one merge walk per ``merged``.

    Args:
        body (OrgBatchGetRequest): Request body for POST /api/v1/orgs/batch-get (#654).

            Many ``getOrg`` reads in one call and one read-bucket token. Duplicate ids
            are deduped, first occurrence wins (the ``verify`` precedent). The cap is
            on the raw list, before the dedupe.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | OrgBatchGetResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: OrgBatchGetRequest,
) -> ErrorDetail | HTTPValidationError | OrgBatchGetResponse | None:
    """Batch Get Orgs

     Read many orgs in one call: one ``getOrg`` answer per distinct id (#654).

    Each result is the outcome ``getOrg`` would give that id: ``ok`` (200, the
    same body and ETag), ``not_modified`` (304 against ``etags[id]``),
    ``merged`` / ``gone`` (410 with or without ``merged_into``), ``not_found``
    (404). One bad id never fails the batch; text Postgres cannot store fails
    the whole call with 422 at the boundary (#663). Results come in request order,
    duplicates dropped after their first occurrence. Set-based: the query count
    does not grow with the number of ids, except one merge walk per ``merged``.

    Args:
        body (OrgBatchGetRequest): Request body for POST /api/v1/orgs/batch-get (#654).

            Many ``getOrg`` reads in one call and one read-bucket token. Duplicate ids
            are deduped, first occurrence wins (the ``verify`` precedent). The cap is
            on the raw list, before the dedupe.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | OrgBatchGetResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: OrgBatchGetRequest,
) -> Response[ErrorDetail | HTTPValidationError | OrgBatchGetResponse]:
    """Batch Get Orgs

     Read many orgs in one call: one ``getOrg`` answer per distinct id (#654).

    Each result is the outcome ``getOrg`` would give that id: ``ok`` (200, the
    same body and ETag), ``not_modified`` (304 against ``etags[id]``),
    ``merged`` / ``gone`` (410 with or without ``merged_into``), ``not_found``
    (404). One bad id never fails the batch; text Postgres cannot store fails
    the whole call with 422 at the boundary (#663). Results come in request order,
    duplicates dropped after their first occurrence. Set-based: the query count
    does not grow with the number of ids, except one merge walk per ``merged``.

    Args:
        body (OrgBatchGetRequest): Request body for POST /api/v1/orgs/batch-get (#654).

            Many ``getOrg`` reads in one call and one read-bucket token. Duplicate ids
            are deduped, first occurrence wins (the ``verify`` precedent). The cap is
            on the raw list, before the dedupe.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | OrgBatchGetResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: OrgBatchGetRequest,
) -> ErrorDetail | HTTPValidationError | OrgBatchGetResponse | None:
    """Batch Get Orgs

     Read many orgs in one call: one ``getOrg`` answer per distinct id (#654).

    Each result is the outcome ``getOrg`` would give that id: ``ok`` (200, the
    same body and ETag), ``not_modified`` (304 against ``etags[id]``),
    ``merged`` / ``gone`` (410 with or without ``merged_into``), ``not_found``
    (404). One bad id never fails the batch; text Postgres cannot store fails
    the whole call with 422 at the boundary (#663). Results come in request order,
    duplicates dropped after their first occurrence. Set-based: the query count
    does not grow with the number of ids, except one merge walk per ``merged``.

    Args:
        body (OrgBatchGetRequest): Request body for POST /api/v1/orgs/batch-get (#654).

            Many ``getOrg`` reads in one call and one read-bucket token. Duplicate ids
            are deduped, first occurrence wins (the ``verify`` precedent). The cap is
            on the raw list, before the dedupe.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | OrgBatchGetResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
