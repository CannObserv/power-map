from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.embedding_write_request import EmbeddingWriteRequest
from ...models.embedding_write_response import EmbeddingWriteResponse
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    person_id: str,
    *,
    body: EmbeddingWriteRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/{person_id}/embeddings".format(
            person_id=quote(str(person_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EmbeddingWriteResponse | ErrorDetail | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingWriteResponse.from_dict(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = ErrorDetail.from_dict(response.json())

        return response_401

    if response.status_code == 403:
        response_403 = ErrorDetail.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorDetail.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorDetail.from_dict(response.json())

        return response_409

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
) -> Response[EmbeddingWriteResponse | ErrorDetail | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingWriteRequest,
) -> Response[EmbeddingWriteResponse | ErrorDetail | HTTPValidationError]:
    """Write Person Embedding

     Write a voice embedding observation for a person.

    Idempotent on the (source_service, source_job_id, source_segment, person_id)
    unique constraint — a duplicate write against an *active* row returns 200 with
    the existing row's id.  409 if the conflicting row is archived (restore or
    change the provenance key first).
    404 if the person does not exist or is archived.
    422 on dimension mismatch, unknown/write-disabled model, or invalid
    embedding (zero vector, non-finite values).

    Args:
        person_id (str):
        body (EmbeddingWriteRequest): Request body for POST /api/v1/people/{id}/embeddings.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingWriteResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingWriteRequest,
) -> EmbeddingWriteResponse | ErrorDetail | HTTPValidationError | None:
    """Write Person Embedding

     Write a voice embedding observation for a person.

    Idempotent on the (source_service, source_job_id, source_segment, person_id)
    unique constraint — a duplicate write against an *active* row returns 200 with
    the existing row's id.  409 if the conflicting row is archived (restore or
    change the provenance key first).
    404 if the person does not exist or is archived.
    422 on dimension mismatch, unknown/write-disabled model, or invalid
    embedding (zero vector, non-finite values).

    Args:
        person_id (str):
        body (EmbeddingWriteRequest): Request body for POST /api/v1/people/{id}/embeddings.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingWriteResponse | ErrorDetail | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingWriteRequest,
) -> Response[EmbeddingWriteResponse | ErrorDetail | HTTPValidationError]:
    """Write Person Embedding

     Write a voice embedding observation for a person.

    Idempotent on the (source_service, source_job_id, source_segment, person_id)
    unique constraint — a duplicate write against an *active* row returns 200 with
    the existing row's id.  409 if the conflicting row is archived (restore or
    change the provenance key first).
    404 if the person does not exist or is archived.
    422 on dimension mismatch, unknown/write-disabled model, or invalid
    embedding (zero vector, non-finite values).

    Args:
        person_id (str):
        body (EmbeddingWriteRequest): Request body for POST /api/v1/people/{id}/embeddings.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingWriteResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingWriteRequest,
) -> EmbeddingWriteResponse | ErrorDetail | HTTPValidationError | None:
    """Write Person Embedding

     Write a voice embedding observation for a person.

    Idempotent on the (source_service, source_job_id, source_segment, person_id)
    unique constraint — a duplicate write against an *active* row returns 200 with
    the existing row's id.  409 if the conflicting row is archived (restore or
    change the provenance key first).
    404 if the person does not exist or is archived.
    422 on dimension mismatch, unknown/write-disabled model, or invalid
    embedding (zero vector, non-finite values).

    Args:
        person_id (str):
        body (EmbeddingWriteRequest): Request body for POST /api/v1/people/{id}/embeddings.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingWriteResponse | ErrorDetail | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            client=client,
            body=body,
        )
    ).parsed
