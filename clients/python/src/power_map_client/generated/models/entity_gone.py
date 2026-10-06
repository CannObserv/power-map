from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.entity_gone_entity_type import EntityGoneEntityType

T = TypeVar("T", bound="EntityGone")


@_attrs_define
class EntityGone:
    """Body of a detail GET's ``410 Gone``: the id was merged away or deleted (#607).

    Read from the ``deleted_entities`` tombstone. ``merged_into`` is the live end
    of the merge chain — the id to re-anchor to — or ``null`` when the id (or the
    chain it leads down) was genuinely deleted. A merge tombstone never expires.

        Attributes:
            id (str):
            entity_type (EntityGoneEntityType):
            deleted_at (datetime.datetime):
            merged_into (None | str): Where the id went: the current id of the row it was merged into, following later
                merges to the end of the chain. null for a genuine delete. May name an archived row, which its own GET still
                answers.
    """

    id: str
    entity_type: EntityGoneEntityType
    deleted_at: datetime.datetime
    merged_into: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        entity_type = self.entity_type.value

        deleted_at = self.deleted_at.isoformat()

        merged_into: None | str
        merged_into = self.merged_into

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "entity_type": entity_type,
                "deleted_at": deleted_at,
                "merged_into": merged_into,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        entity_type = EntityGoneEntityType(d.pop("entity_type"))

        deleted_at = datetime.datetime.fromisoformat(d.pop("deleted_at"))

        def _parse_merged_into(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        merged_into = _parse_merged_into(d.pop("merged_into"))

        entity_gone = cls(
            id=id,
            entity_type=entity_type,
            deleted_at=deleted_at,
            merged_into=merged_into,
        )

        entity_gone.additional_properties = d
        return entity_gone

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
