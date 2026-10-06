from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.org_acronym import OrgAcronym
    from ..models.org_identifier import OrgIdentifier
    from ..models.org_jurisdiction_affiliation import OrgJurisdictionAffiliation
    from ..models.org_lifespan import OrgLifespan
    from ..models.org_name import OrgName


T = TypeVar("T", bound="OrgDetail")


@_attrs_define
class OrgDetail:
    """Full org record including name variants, acronyms, identifiers, and affiliations.

    Attributes:
        id (str):
        lifespan (OrgLifespan): Org validity window (#469): start from the earliest `founded` event
            (earliest date within its precision), end from `v_org_lifespan` (latest
            date within precision of the earliest dissolved/merged_with/dated
            succeeded_by event). Either side null when no dated event bounds it.
        active (bool):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        name (None | str | Unset):
        acronym (None | str | Unset):
        slug (None | str | Unset):
        parent_id (None | str | Unset):
        archived_at (datetime.datetime | None | Unset):
        succeeds (None | str | Unset):
        succeeded_by (None | str | Unset):
        names (list[OrgName] | Unset):
        acronyms (list[OrgAcronym] | Unset):
        identifiers (list[OrgIdentifier] | Unset):
        jurisdiction_affiliations (list[OrgJurisdictionAffiliation] | Unset):
    """

    id: str
    lifespan: OrgLifespan
    active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    name: None | str | Unset = UNSET
    acronym: None | str | Unset = UNSET
    slug: None | str | Unset = UNSET
    parent_id: None | str | Unset = UNSET
    archived_at: datetime.datetime | None | Unset = UNSET
    succeeds: None | str | Unset = UNSET
    succeeded_by: None | str | Unset = UNSET
    names: list[OrgName] | Unset = UNSET
    acronyms: list[OrgAcronym] | Unset = UNSET
    identifiers: list[OrgIdentifier] | Unset = UNSET
    jurisdiction_affiliations: list[OrgJurisdictionAffiliation] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        lifespan = self.lifespan.to_dict()

        active = self.active

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        acronym: None | str | Unset
        if isinstance(self.acronym, Unset):
            acronym = UNSET
        else:
            acronym = self.acronym

        slug: None | str | Unset
        if isinstance(self.slug, Unset):
            slug = UNSET
        else:
            slug = self.slug

        parent_id: None | str | Unset
        if isinstance(self.parent_id, Unset):
            parent_id = UNSET
        else:
            parent_id = self.parent_id

        archived_at: None | str | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        elif isinstance(self.archived_at, datetime.datetime):
            archived_at = self.archived_at.isoformat()
        else:
            archived_at = self.archived_at

        succeeds: None | str | Unset
        if isinstance(self.succeeds, Unset):
            succeeds = UNSET
        else:
            succeeds = self.succeeds

        succeeded_by: None | str | Unset
        if isinstance(self.succeeded_by, Unset):
            succeeded_by = UNSET
        else:
            succeeded_by = self.succeeded_by

        names: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.names, Unset):
            names = []
            for names_item_data in self.names:
                names_item = names_item_data.to_dict()
                names.append(names_item)

        acronyms: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.acronyms, Unset):
            acronyms = []
            for acronyms_item_data in self.acronyms:
                acronyms_item = acronyms_item_data.to_dict()
                acronyms.append(acronyms_item)

        identifiers: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.identifiers, Unset):
            identifiers = []
            for identifiers_item_data in self.identifiers:
                identifiers_item = identifiers_item_data.to_dict()
                identifiers.append(identifiers_item)

        jurisdiction_affiliations: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.jurisdiction_affiliations, Unset):
            jurisdiction_affiliations = []
            for jurisdiction_affiliations_item_data in self.jurisdiction_affiliations:
                jurisdiction_affiliations_item = jurisdiction_affiliations_item_data.to_dict()
                jurisdiction_affiliations.append(jurisdiction_affiliations_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "lifespan": lifespan,
                "active": active,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if acronym is not UNSET:
            field_dict["acronym"] = acronym
        if slug is not UNSET:
            field_dict["slug"] = slug
        if parent_id is not UNSET:
            field_dict["parent_id"] = parent_id
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at
        if succeeds is not UNSET:
            field_dict["succeeds"] = succeeds
        if succeeded_by is not UNSET:
            field_dict["succeeded_by"] = succeeded_by
        if names is not UNSET:
            field_dict["names"] = names
        if acronyms is not UNSET:
            field_dict["acronyms"] = acronyms
        if identifiers is not UNSET:
            field_dict["identifiers"] = identifiers
        if jurisdiction_affiliations is not UNSET:
            field_dict["jurisdiction_affiliations"] = jurisdiction_affiliations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.org_acronym import OrgAcronym  # noqa: PLC0415
        from ..models.org_identifier import OrgIdentifier  # noqa: PLC0415
        from ..models.org_jurisdiction_affiliation import (
            OrgJurisdictionAffiliation,  # noqa: PLC0415
        )
        from ..models.org_lifespan import OrgLifespan  # noqa: PLC0415
        from ..models.org_name import OrgName  # noqa: PLC0415

        d = dict(src_dict)
        id = d.pop("id")

        lifespan = OrgLifespan.from_dict(d.pop("lifespan"))

        active = d.pop("active")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_acronym(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        acronym = _parse_acronym(d.pop("acronym", UNSET))

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        def _parse_parent_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent_id = _parse_parent_id(d.pop("parent_id", UNSET))

        def _parse_archived_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                archived_at_type_0 = datetime.datetime.fromisoformat(data)

                return archived_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        def _parse_succeeds(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        succeeds = _parse_succeeds(d.pop("succeeds", UNSET))

        def _parse_succeeded_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        succeeded_by = _parse_succeeded_by(d.pop("succeeded_by", UNSET))

        _names = d.pop("names", UNSET)
        names: list[OrgName] | Unset = UNSET
        if _names is not UNSET:
            names = []
            for names_item_data in _names:
                names_item = OrgName.from_dict(names_item_data)

                names.append(names_item)

        _acronyms = d.pop("acronyms", UNSET)
        acronyms: list[OrgAcronym] | Unset = UNSET
        if _acronyms is not UNSET:
            acronyms = []
            for acronyms_item_data in _acronyms:
                acronyms_item = OrgAcronym.from_dict(acronyms_item_data)

                acronyms.append(acronyms_item)

        _identifiers = d.pop("identifiers", UNSET)
        identifiers: list[OrgIdentifier] | Unset = UNSET
        if _identifiers is not UNSET:
            identifiers = []
            for identifiers_item_data in _identifiers:
                identifiers_item = OrgIdentifier.from_dict(identifiers_item_data)

                identifiers.append(identifiers_item)

        _jurisdiction_affiliations = d.pop("jurisdiction_affiliations", UNSET)
        jurisdiction_affiliations: list[OrgJurisdictionAffiliation] | Unset = UNSET
        if _jurisdiction_affiliations is not UNSET:
            jurisdiction_affiliations = []
            for jurisdiction_affiliations_item_data in _jurisdiction_affiliations:
                jurisdiction_affiliations_item = OrgJurisdictionAffiliation.from_dict(
                    jurisdiction_affiliations_item_data
                )

                jurisdiction_affiliations.append(jurisdiction_affiliations_item)

        org_detail = cls(
            id=id,
            lifespan=lifespan,
            active=active,
            created_at=created_at,
            updated_at=updated_at,
            name=name,
            acronym=acronym,
            slug=slug,
            parent_id=parent_id,
            archived_at=archived_at,
            succeeds=succeeds,
            succeeded_by=succeeded_by,
            names=names,
            acronyms=acronyms,
            identifiers=identifiers,
            jurisdiction_affiliations=jurisdiction_affiliations,
        )

        org_detail.additional_properties = d
        return org_detail

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
