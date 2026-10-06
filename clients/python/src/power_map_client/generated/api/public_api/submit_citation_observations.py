from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.citation_observations_request import CitationObservationsRequest
from ...models.citation_observations_response import CitationObservationsResponse
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    entity_type: str,
    entity_id: str,
    *,
    body: CitationObservationsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/citations/{entity_type}/{entity_id}/observations".format(
            entity_type=quote(str(entity_type), safe=""),
            entity_id=quote(str(entity_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CitationObservationsResponse | ErrorDetail | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = CitationObservationsResponse.from_dict(response.json())

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
) -> Response[CitationObservationsResponse | ErrorDetail | HTTPValidationError]:
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
    body: CitationObservationsRequest,
) -> Response[CitationObservationsResponse | ErrorDetail | HTTPValidationError]:
    """Submit Citation Observations

     Observe/retract source citations on an entity, **partial-success** (#319).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    typo'd ``field_name`` → ``citable_field_unknown``, or a not-yet-anchored target
    → ``entity_unresolved``) never rolls back its siblings. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → a
    natural-key observe (identity = entity/field/url; refine-or-create).

    Args:
        entity_type (str):
        entity_id (str):
        body (CitationObservationsRequest): Payload for POST
            /api/v1/citations/{entity_type}/{entity_id}/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CitationObservationsResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        entity_id=entity_id,
        body=body,
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
    body: CitationObservationsRequest,
) -> CitationObservationsResponse | ErrorDetail | HTTPValidationError | None:
    """Submit Citation Observations

     Observe/retract source citations on an entity, **partial-success** (#319).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    typo'd ``field_name`` → ``citable_field_unknown``, or a not-yet-anchored target
    → ``entity_unresolved``) never rolls back its siblings. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → a
    natural-key observe (identity = entity/field/url; refine-or-create).

    Args:
        entity_type (str):
        entity_id (str):
        body (CitationObservationsRequest): Payload for POST
            /api/v1/citations/{entity_type}/{entity_id}/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CitationObservationsResponse | ErrorDetail | HTTPValidationError
    """

    return sync_detailed(
        entity_type=entity_type,
        entity_id=entity_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    body: CitationObservationsRequest,
) -> Response[CitationObservationsResponse | ErrorDetail | HTTPValidationError]:
    """Submit Citation Observations

     Observe/retract source citations on an entity, **partial-success** (#319).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    typo'd ``field_name`` → ``citable_field_unknown``, or a not-yet-anchored target
    → ``entity_unresolved``) never rolls back its siblings. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → a
    natural-key observe (identity = entity/field/url; refine-or-create).

    Args:
        entity_type (str):
        entity_id (str):
        body (CitationObservationsRequest): Payload for POST
            /api/v1/citations/{entity_type}/{entity_id}/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CitationObservationsResponse | ErrorDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        entity_id=entity_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    entity_type: str,
    entity_id: str,
    *,
    client: AuthenticatedClient,
    body: CitationObservationsRequest,
) -> CitationObservationsResponse | ErrorDetail | HTTPValidationError | None:
    """Submit Citation Observations

     Observe/retract source citations on an entity, **partial-success** (#319).

    Each claim lands independently under its own savepoint: one rejection (e.g. a
    typo'd ``field_name`` → ``citable_field_unknown``, or a not-yet-anchored target
    → ``entity_unresolved``) never rolls back its siblings. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → a
    natural-key observe (identity = entity/field/url; refine-or-create).

    Args:
        entity_type (str):
        entity_id (str):
        body (CitationObservationsRequest): Payload for POST
            /api/v1/citations/{entity_type}/{entity_id}/observations.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CitationObservationsResponse | ErrorDetail | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            entity_type=entity_type,
            entity_id=entity_id,
            client=client,
            body=body,
        )
    ).parsed
