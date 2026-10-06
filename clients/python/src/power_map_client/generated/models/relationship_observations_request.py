from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relationship_observation_item import RelationshipObservationItem


T = TypeVar("T", bound="RelationshipObservationsRequest")


@_attrs_define
class RelationshipObservationsRequest:
    """Payload for POST /api/v1/assignment-relationships/observations.

    Attributes:
        relationships (list[RelationshipObservationItem] | Unset):
    """

    relationships: list[RelationshipObservationItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        relationships: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.relationships, Unset):
            relationships = []
            for relationships_item_data in self.relationships:
                relationships_item = relationships_item_data.to_dict()
                relationships.append(relationships_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if relationships is not UNSET:
            field_dict["relationships"] = relationships

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relationship_observation_item import (
            RelationshipObservationItem,  # noqa: PLC0415
        )

        d = dict(src_dict)
        _relationships = d.pop("relationships", UNSET)
        relationships: list[RelationshipObservationItem] | Unset = UNSET
        if _relationships is not UNSET:
            relationships = []
            for relationships_item_data in _relationships:
                relationships_item = RelationshipObservationItem.from_dict(relationships_item_data)

                relationships.append(relationships_item)

        relationship_observations_request = cls(
            relationships=relationships,
        )

        relationship_observations_request.additional_properties = d
        return relationship_observations_request

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
