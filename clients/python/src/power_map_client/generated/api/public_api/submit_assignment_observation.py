from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.assignment_observation_request import AssignmentObservationRequest
from ...models.error_detail import ErrorDetail
from ...models.http_validation_error import HTTPValidationError
from ...models.observation_response import ObservationResponse
from ...types import Response


def _get_kwargs(
    *,
    body: AssignmentObservationRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/assignments/observations",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorDetail | HTTPValidationError | ObservationResponse | None:
    if response.status_code == 200:
        response_200 = ObservationResponse.from_dict(response.json())

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
) -> Response[ErrorDetail | HTTPValidationError | ObservationResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: AssignmentObservationRequest,
) -> Response[ErrorDetail | HTTPValidationError | ObservationResponse]:
    """Submit Assignment Observation

     Submit an assignment observation.

    Resolves by (person_id, role_id, start_date) or by pm_assignment_id.
    In pm_assignment_id mode supplied fields **update the tenure in place**
    (#311, supersedes the #289 NULL→dated-only backfill): start_date moves,
    an explicit ``end_date: null`` clears (reopen), is_current sets/clears —
    gated on source_key_id provenance. In standard mode an auto-attach applies
    only the open-tenure close; other deltas are echoed back in ``unapplied``.

    ``op="retract"`` (#391) archives the id-addressed tenure instead — the
    correction for a produced **artifact** (a tenure that never happened), which
    closing cannot express and un-producing would only orphan. Always id-addressed
    (natural-key → ``invalid``), refine payload and ancillary ignored, re-emit is a
    quiet ``auto-attached`` no-op.

    The retract is **authoritative**: a later natural-key re-observation attaches
    to the archived row rather than resurrecting it, and that attach **writes
    nothing at all** — bound deltas are withheld and ancillary (``links`` /
    ``contact_methods`` / ``addresses``) is skipped rather than pinned to a
    retracted row. Every withheld field name comes back in ``unapplied`` so a
    producer still emitting the tenure is told rather than silently no-op'd.

    Both archived outcomes — that attach and a re-emitted ``op="retract"`` — also
    set ``attached_archived: true`` (#477). ``auto-attached`` alone cannot
    separate them from a healthy attach to a live tenure, and ``unapplied`` never
    meant "archived"; inferring it from a field name there cost the downstream
    producer a month anchored to a retracted row (#474).

    ``provenance_claimed: true`` (#478) says this observation stamped
    ``source_key_id`` onto a row that had none. Asserting a span **identical** to
    what an unowned row already stores now claims it — before, provenance was
    only ever claimed as a side effect of a value change, so a correct row was
    unclaimable without falsifying a date. An *owned* row (same-source or
    foreign) is untouched by an identical assertion, as #311 CR round 1 requires.

    Args:
        body (AssignmentObservationRequest): Payload for POST /api/v1/assignments/observations.

            Two resolution modes (mutually exclusive):
              - Standard:  person_id + role_id (match or create by person+role+start_date)
              - PM-native: identifier_type="pm_assignment_id" + identifier_value=<assignment ULID>
                           (attach to known assignment; never creates; person_id/role_id not required)

            ``op`` is ``observe`` (default) or ``retract`` (#391). ``retract`` archives the
            id-addressed assignment — always PM-native, refine payload ignored.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | ObservationResponse]
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
    body: AssignmentObservationRequest,
) -> ErrorDetail | HTTPValidationError | ObservationResponse | None:
    """Submit Assignment Observation

     Submit an assignment observation.

    Resolves by (person_id, role_id, start_date) or by pm_assignment_id.
    In pm_assignment_id mode supplied fields **update the tenure in place**
    (#311, supersedes the #289 NULL→dated-only backfill): start_date moves,
    an explicit ``end_date: null`` clears (reopen), is_current sets/clears —
    gated on source_key_id provenance. In standard mode an auto-attach applies
    only the open-tenure close; other deltas are echoed back in ``unapplied``.

    ``op="retract"`` (#391) archives the id-addressed tenure instead — the
    correction for a produced **artifact** (a tenure that never happened), which
    closing cannot express and un-producing would only orphan. Always id-addressed
    (natural-key → ``invalid``), refine payload and ancillary ignored, re-emit is a
    quiet ``auto-attached`` no-op.

    The retract is **authoritative**: a later natural-key re-observation attaches
    to the archived row rather than resurrecting it, and that attach **writes
    nothing at all** — bound deltas are withheld and ancillary (``links`` /
    ``contact_methods`` / ``addresses``) is skipped rather than pinned to a
    retracted row. Every withheld field name comes back in ``unapplied`` so a
    producer still emitting the tenure is told rather than silently no-op'd.

    Both archived outcomes — that attach and a re-emitted ``op="retract"`` — also
    set ``attached_archived: true`` (#477). ``auto-attached`` alone cannot
    separate them from a healthy attach to a live tenure, and ``unapplied`` never
    meant "archived"; inferring it from a field name there cost the downstream
    producer a month anchored to a retracted row (#474).

    ``provenance_claimed: true`` (#478) says this observation stamped
    ``source_key_id`` onto a row that had none. Asserting a span **identical** to
    what an unowned row already stores now claims it — before, provenance was
    only ever claimed as a side effect of a value change, so a correct row was
    unclaimable without falsifying a date. An *owned* row (same-source or
    foreign) is untouched by an identical assertion, as #311 CR round 1 requires.

    Args:
        body (AssignmentObservationRequest): Payload for POST /api/v1/assignments/observations.

            Two resolution modes (mutually exclusive):
              - Standard:  person_id + role_id (match or create by person+role+start_date)
              - PM-native: identifier_type="pm_assignment_id" + identifier_value=<assignment ULID>
                           (attach to known assignment; never creates; person_id/role_id not required)

            ``op`` is ``observe`` (default) or ``retract`` (#391). ``retract`` archives the
            id-addressed assignment — always PM-native, refine payload ignored.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | ObservationResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: AssignmentObservationRequest,
) -> Response[ErrorDetail | HTTPValidationError | ObservationResponse]:
    """Submit Assignment Observation

     Submit an assignment observation.

    Resolves by (person_id, role_id, start_date) or by pm_assignment_id.
    In pm_assignment_id mode supplied fields **update the tenure in place**
    (#311, supersedes the #289 NULL→dated-only backfill): start_date moves,
    an explicit ``end_date: null`` clears (reopen), is_current sets/clears —
    gated on source_key_id provenance. In standard mode an auto-attach applies
    only the open-tenure close; other deltas are echoed back in ``unapplied``.

    ``op="retract"`` (#391) archives the id-addressed tenure instead — the
    correction for a produced **artifact** (a tenure that never happened), which
    closing cannot express and un-producing would only orphan. Always id-addressed
    (natural-key → ``invalid``), refine payload and ancillary ignored, re-emit is a
    quiet ``auto-attached`` no-op.

    The retract is **authoritative**: a later natural-key re-observation attaches
    to the archived row rather than resurrecting it, and that attach **writes
    nothing at all** — bound deltas are withheld and ancillary (``links`` /
    ``contact_methods`` / ``addresses``) is skipped rather than pinned to a
    retracted row. Every withheld field name comes back in ``unapplied`` so a
    producer still emitting the tenure is told rather than silently no-op'd.

    Both archived outcomes — that attach and a re-emitted ``op="retract"`` — also
    set ``attached_archived: true`` (#477). ``auto-attached`` alone cannot
    separate them from a healthy attach to a live tenure, and ``unapplied`` never
    meant "archived"; inferring it from a field name there cost the downstream
    producer a month anchored to a retracted row (#474).

    ``provenance_claimed: true`` (#478) says this observation stamped
    ``source_key_id`` onto a row that had none. Asserting a span **identical** to
    what an unowned row already stores now claims it — before, provenance was
    only ever claimed as a side effect of a value change, so a correct row was
    unclaimable without falsifying a date. An *owned* row (same-source or
    foreign) is untouched by an identical assertion, as #311 CR round 1 requires.

    Args:
        body (AssignmentObservationRequest): Payload for POST /api/v1/assignments/observations.

            Two resolution modes (mutually exclusive):
              - Standard:  person_id + role_id (match or create by person+role+start_date)
              - PM-native: identifier_type="pm_assignment_id" + identifier_value=<assignment ULID>
                           (attach to known assignment; never creates; person_id/role_id not required)

            ``op`` is ``observe`` (default) or ``retract`` (#391). ``retract`` archives the
            id-addressed assignment — always PM-native, refine payload ignored.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorDetail | HTTPValidationError | ObservationResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: AssignmentObservationRequest,
) -> ErrorDetail | HTTPValidationError | ObservationResponse | None:
    """Submit Assignment Observation

     Submit an assignment observation.

    Resolves by (person_id, role_id, start_date) or by pm_assignment_id.
    In pm_assignment_id mode supplied fields **update the tenure in place**
    (#311, supersedes the #289 NULL→dated-only backfill): start_date moves,
    an explicit ``end_date: null`` clears (reopen), is_current sets/clears —
    gated on source_key_id provenance. In standard mode an auto-attach applies
    only the open-tenure close; other deltas are echoed back in ``unapplied``.

    ``op="retract"`` (#391) archives the id-addressed tenure instead — the
    correction for a produced **artifact** (a tenure that never happened), which
    closing cannot express and un-producing would only orphan. Always id-addressed
    (natural-key → ``invalid``), refine payload and ancillary ignored, re-emit is a
    quiet ``auto-attached`` no-op.

    The retract is **authoritative**: a later natural-key re-observation attaches
    to the archived row rather than resurrecting it, and that attach **writes
    nothing at all** — bound deltas are withheld and ancillary (``links`` /
    ``contact_methods`` / ``addresses``) is skipped rather than pinned to a
    retracted row. Every withheld field name comes back in ``unapplied`` so a
    producer still emitting the tenure is told rather than silently no-op'd.

    Both archived outcomes — that attach and a re-emitted ``op="retract"`` — also
    set ``attached_archived: true`` (#477). ``auto-attached`` alone cannot
    separate them from a healthy attach to a live tenure, and ``unapplied`` never
    meant "archived"; inferring it from a field name there cost the downstream
    producer a month anchored to a retracted row (#474).

    ``provenance_claimed: true`` (#478) says this observation stamped
    ``source_key_id`` onto a row that had none. Asserting a span **identical** to
    what an unowned row already stores now claims it — before, provenance was
    only ever claimed as a side effect of a value change, so a correct row was
    unclaimable without falsifying a date. An *owned* row (same-source or
    foreign) is untouched by an identical assertion, as #311 CR round 1 requires.

    Args:
        body (AssignmentObservationRequest): Payload for POST /api/v1/assignments/observations.

            Two resolution modes (mutually exclusive):
              - Standard:  person_id + role_id (match or create by person+role+start_date)
              - PM-native: identifier_type="pm_assignment_id" + identifier_value=<assignment ULID>
                           (attach to known assignment; never creates; person_id/role_id not required)

            ``op`` is ``observe`` (default) or ``retract`` (#391). ``retract`` archives the
            id-addressed assignment — always PM-native, refine payload ignored.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorDetail | HTTPValidationError | ObservationResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
