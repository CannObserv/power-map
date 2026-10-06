from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.relationship_observations_request import RelationshipObservationsRequest
from ...models.relationship_observations_response import (
    RelationshipObservationsResponse,
)
from ...types import Response


def _get_kwargs(
    *,
    body: RelationshipObservationsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/assignment-relationships/observations",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorDetail | HTTPValidationError | RelationshipObservationsResponse | None:
    if response.status_code == 200:
        response_200 = RelationshipObservationsResponse.from_dict(response.json())

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
) -> Response[ErrorDetail | HTTPValidationError | RelationshipObservationsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: RelationshipObservationsRequest,
) -> Response[ErrorDetail | HTTPValidationError | RelationshipObservationsResponse]:
    """Submit Relationship Observations

     Observe/retract role-assignment relationships, **partial-success** (#301).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    not-yet-anchored endpoint → ``assignment_unresolved``, or a foreign owner →
    ``provenance_conflict``) never rolls back its siblings. ``pm_relationship_id``
    addresses an existing edge for id-scoped refine/retract; absent → a natural-key
    observe (identity = from + to + rel_type; refine-or-create). Temporal windows
    are recorded freely here — the daily audit reconciles against endpoint windows.

    Args:
        body (RelationshipObservationsRequest): Payload for POST /api/v1/assignment-
            relationships/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | RelationshipObservationsResponse]
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
    body: RelationshipObservationsRequest,
) -> ErrorDetail | HTTPValidationError | RelationshipObservationsResponse | None:
    """Submit Relationship Observations

     Observe/retract role-assignment relationships, **partial-success** (#301).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    not-yet-anchored endpoint → ``assignment_unresolved``, or a foreign owner →
    ``provenance_conflict``) never rolls back its siblings. ``pm_relationship_id``
    addresses an existing edge for id-scoped refine/retract; absent → a natural-key
    observe (identity = from + to + rel_type; refine-or-create). Temporal windows
    are recorded freely here — the daily audit reconciles against endpoint windows.

    Args:
        body (RelationshipObservationsRequest): Payload for POST /api/v1/assignment-
            relationships/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | RelationshipObservationsResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: RelationshipObservationsRequest,
) -> Response[ErrorDetail | HTTPValidationError | RelationshipObservationsResponse]:
    """Submit Relationship Observations

     Observe/retract role-assignment relationships, **partial-success** (#301).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    not-yet-anchored endpoint → ``assignment_unresolved``, or a foreign owner →
    ``provenance_conflict``) never rolls back its siblings. ``pm_relationship_id``
    addresses an existing edge for id-scoped refine/retract; absent → a natural-key
    observe (identity = from + to + rel_type; refine-or-create). Temporal windows
    are recorded freely here — the daily audit reconciles against endpoint windows.

    Args:
        body (RelationshipObservationsRequest): Payload for POST /api/v1/assignment-
            relationships/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | RelationshipObservationsResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: RelationshipObservationsRequest,
) -> ErrorDetail | HTTPValidationError | RelationshipObservationsResponse | None:
    """Submit Relationship Observations

     Observe/retract role-assignment relationships, **partial-success** (#301).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    not-yet-anchored endpoint → ``assignment_unresolved``, or a foreign owner →
    ``provenance_conflict``) never rolls back its siblings. ``pm_relationship_id``
    addresses an existing edge for id-scoped refine/retract; absent → a natural-key
    observe (identity = from + to + rel_type; refine-or-create). Temporal windows
    are recorded freely here — the daily audit reconciles against endpoint windows.

    Args:
        body (RelationshipObservationsRequest): Payload for POST /api/v1/assignment-
            relationships/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | RelationshipObservationsResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
