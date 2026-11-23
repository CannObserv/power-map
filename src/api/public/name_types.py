"""Public API: person and organization name-type catalogs (#857).

Serves the name-type registry (``src.core.name_types``): for each type, its
meaning, when to use it and when not to, an example, and — for person types —
how power-map treats it. The connecting projects' one reference: a producer
choosing between ``variant`` and ``misspelling`` reads it here rather than
hard-coding its own copy.

Backed by code, not a table: the vocabulary changes only with the schema
``CHECK``, in a release. The read schemas' ``name_type`` stays a plain string,
so a type added later never breaks an older generated client reading a person.
"""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.api.public.deps import require_api_key
from src.api.public.etag import NOT_MODIFIED, catalog_validator, conditional_response
from src.api.public.schemas import (
    OrganizationNameTypeEntry,
    OrganizationNameTypesResponse,
    PersonNameTypeEntry,
    PersonNameTypesResponse,
)
from src.core.name_types import ORG_NAME_TYPE_SPECS, PERSON_NAME_TYPE_SPECS

router = APIRouter()

# Serialized once: the registry is immutable for the life of the process. The
# content-hash validator then changes exactly when a release changes an entry.
_PERSON_ENTRIES = [PersonNameTypeEntry(**asdict(s)) for s in PERSON_NAME_TYPE_SPECS]
_ORG_ENTRIES = [OrganizationNameTypeEntry(**asdict(s)) for s in ORG_NAME_TYPE_SPECS]
_PERSON_ETAG = catalog_validator(e.model_dump() for e in _PERSON_ENTRIES)
_ORG_ETAG = catalog_validator(e.model_dump() for e in _ORG_ENTRIES)


@router.get(
    "/person-name-types",
    response_model=PersonNameTypesResponse,
    operation_id="list_person_name_types",
    responses=NOT_MODIFIED,
)
async def list_person_name_types(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
) -> Any:
    """Return every person name type: meaning, use, and how PM treats it."""
    cached = conditional_response(request, response, _PERSON_ETAG)
    if cached is not None:
        return cached
    return PersonNameTypesResponse(data=_PERSON_ENTRIES)


@router.get(
    "/organization-name-types",
    response_model=OrganizationNameTypesResponse,
    operation_id="list_organization_name_types",
    responses=NOT_MODIFIED,
)
async def list_organization_name_types(
    request: Request,
    response: Response,
    _user_id: str = Depends(require_api_key),
) -> Any:
    """Return every organization name type: meaning and use."""
    cached = conditional_response(request, response, _ORG_ETAG)
    if cached is not None:
        return cached
    return OrganizationNameTypesResponse(data=_ORG_ENTRIES)
