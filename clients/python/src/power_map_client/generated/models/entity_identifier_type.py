from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.entity_identifier_type_entity_type import EntityIdentifierTypeEntityType

T = TypeVar("T", bound="EntityIdentifierType")


@_attrs_define
class EntityIdentifierType:
    """An identifier type — the vocabulary a producer addresses entities by (#459).

    ``is_internal`` is the field worth reading before writing an observation. An
    internal type (the ``pm_*`` family) **addresses** an existing entity and can
    never mint one — a miss is rejected ``pm_id_not_found`` — and it is refused
    outright in ``additional_identifiers`` (an internal type is how you *reach*
    an entity, not a scheme you attach to one). Every other type auto-attaches
    on a known value and creates on an unknown one.

        Attributes:
            id (str):
            slug (str):
            entity_type (EntityIdentifierTypeEntityType):
            display_name (str):
            full_name (str):
            is_internal (bool):
    """

    id: str
    slug: str
    entity_type: EntityIdentifierTypeEntityType
    display_name: str
    full_name: str
    is_internal: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        slug = self.slug

        entity_type = self.entity_type.value

        display_name = self.display_name

        full_name = self.full_name

        is_internal = self.is_internal

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "slug": slug,
                "entity_type": entity_type,
                "display_name": display_name,
                "full_name": full_name,
                "is_internal": is_internal,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        slug = d.pop("slug")

        entity_type = EntityIdentifierTypeEntityType(d.pop("entity_type"))

        display_name = d.pop("display_name")

        full_name = d.pop("full_name")

        is_internal = d.pop("is_internal")

        entity_identifier_type = cls(
            id=id,
            slug=slug,
            entity_type=entity_type,
            display_name=display_name,
            full_name=full_name,
            is_internal=is_internal,
        )

        entity_identifier_type.additional_properties = d
        return entity_identifier_type

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
