from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.identify_batch_request import IdentifyBatchRequest
from ...models.identify_batch_response import IdentifyBatchResponse
from ...types import Response


def _get_kwargs(
    *,
    body: IdentifyBatchRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/identify-batch",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorDetail | HTTPValidationError | IdentifyBatchResponse | None:
    if response.status_code == 200:
        response_200 = IdentifyBatchResponse.from_dict(response.json())

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
) -> Response[ErrorDetail | HTTPValidationError | IdentifyBatchResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: IdentifyBatchRequest,
) -> Response[ErrorDetail | HTTPValidationError | IdentifyBatchResponse]:
    """Identify Person Batch

     Return the top-k matches for each of N query embeddings (#638).

    Multi-embedding form of ``/identify`` — one group per query embedding, in
    ``embeddings`` request order, each holding what ``/identify`` returns for
    that embedding alone — exact-distance ties, which ``/identify`` leaves in
    index order, are ordered by ``embedding_id``.  One SQL round-trip and one
    read-bucket token regardless of N.

    Identify semantics, not verify's: an unknown or non-queryable model yields
    ``matches: []`` in every group.  422 when any embedding is invalid (zero
    vector, non-finite values) or its dimension mismatches the model (the
    detail names the failing index) — the whole call, never a partial result.

    Args:
        body (IdentifyBatchRequest): Request body for POST /api/v1/people/identify-batch (#638).

            Open-set identify for N query embeddings in one call — collapses the
            per-centroid identify loop into one read-bucket token.  The cap matches
            verify-batch; with ``top_k`` clamped to 25 it bounds a call at 1,250
            matches.  Every field applies to the whole batch, so per-batch context
            hints (observo#235) belong beside them, never inside ``embeddings``.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | IdentifyBatchResponse]
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
    body: IdentifyBatchRequest,
) -> ErrorDetail | HTTPValidationError | IdentifyBatchResponse | None:
    """Identify Person Batch

     Return the top-k matches for each of N query embeddings (#638).

    Multi-embedding form of ``/identify`` — one group per query embedding, in
    ``embeddings`` request order, each holding what ``/identify`` returns for
    that embedding alone — exact-distance ties, which ``/identify`` leaves in
    index order, are ordered by ``embedding_id``.  One SQL round-trip and one
    read-bucket token regardless of N.

    Identify semantics, not verify's: an unknown or non-queryable model yields
    ``matches: []`` in every group.  422 when any embedding is invalid (zero
    vector, non-finite values) or its dimension mismatches the model (the
    detail names the failing index) — the whole call, never a partial result.

    Args:
        body (IdentifyBatchRequest): Request body for POST /api/v1/people/identify-batch (#638).

            Open-set identify for N query embeddings in one call — collapses the
            per-centroid identify loop into one read-bucket token.  The cap matches
            verify-batch; with ``top_k`` clamped to 25 it bounds a call at 1,250
            matches.  Every field applies to the whole batch, so per-batch context
            hints (observo#235) belong beside them, never inside ``embeddings``.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | IdentifyBatchResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: IdentifyBatchRequest,
) -> Response[ErrorDetail | HTTPValidationError | IdentifyBatchResponse]:
    """Identify Person Batch

     Return the top-k matches for each of N query embeddings (#638).

    Multi-embedding form of ``/identify`` — one group per query embedding, in
    ``embeddings`` request order, each holding what ``/identify`` returns for
    that embedding alone — exact-distance ties, which ``/identify`` leaves in
    index order, are ordered by ``embedding_id``.  One SQL round-trip and one
    read-bucket token regardless of N.

    Identify semantics, not verify's: an unknown or non-queryable model yields
    ``matches: []`` in every group.  422 when any embedding is invalid (zero
    vector, non-finite values) or its dimension mismatches the model (the
    detail names the failing index) — the whole call, never a partial result.

    Args:
        body (IdentifyBatchRequest): Request body for POST /api/v1/people/identify-batch (#638).

            Open-set identify for N query embeddings in one call — collapses the
            per-centroid identify loop into one read-bucket token.  The cap matches
            verify-batch; with ``top_k`` clamped to 25 it bounds a call at 1,250
            matches.  Every field applies to the whole batch, so per-batch context
            hints (observo#235) belong beside them, never inside ``embeddings``.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | IdentifyBatchResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: IdentifyBatchRequest,
) -> ErrorDetail | HTTPValidationError | IdentifyBatchResponse | None:
    """Identify Person Batch

     Return the top-k matches for each of N query embeddings (#638).

    Multi-embedding form of ``/identify`` — one group per query embedding, in
    ``embeddings`` request order, each holding what ``/identify`` returns for
    that embedding alone — exact-distance ties, which ``/identify`` leaves in
    index order, are ordered by ``embedding_id``.  One SQL round-trip and one
    read-bucket token regardless of N.

    Identify semantics, not verify's: an unknown or non-queryable model yields
    ``matches: []`` in every group.  422 when any embedding is invalid (zero
    vector, non-finite values) or its dimension mismatches the model (the
    detail names the failing index) — the whole call, never a partial result.

    Args:
        body (IdentifyBatchRequest): Request body for POST /api/v1/people/identify-batch (#638).

            Open-set identify for N query embeddings in one call — collapses the
            per-centroid identify loop into one read-bucket token.  The cap matches
            verify-batch; with ``top_k`` clamped to 25 it bounds a call at 1,250
            matches.  Every field applies to the whole batch, so per-batch context
            hints (observo#235) belong beside them, never inside ``embeddings``.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | IdentifyBatchResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
