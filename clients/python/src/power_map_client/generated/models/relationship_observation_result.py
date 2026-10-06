from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RelationshipObservationResult")


@_attrs_define
class RelationshipObservationResult:
    """Per-relationship outcome (#301). ``reason`` is a machine-readable slug on rejection.

    Attributes:
        disposition (str):
        relationship_id (None | str | Unset):
        reason (None | str | Unset):
        attached_archived (bool | None | Unset):
    """

    disposition: str
    relationship_id: None | str | Unset = UNSET
    reason: None | str | Unset = UNSET
    attached_archived: bool | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        disposition = self.disposition

        relationship_id: None | str | Unset
        if isinstance(self.relationship_id, Unset):
            relationship_id = UNSET
        else:
            relationship_id = self.relationship_id

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        attached_archived: bool | None | Unset
        if isinstance(self.attached_archived, Unset):
            attached_archived = UNSET
        else:
            attached_archived = self.attached_archived

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "disposition": disposition,
            }
        )
        if relationship_id is not UNSET:
            field_dict["relationship_id"] = relationship_id
        if reason is not UNSET:
            field_dict["reason"] = reason
        if attached_archived is not UNSET:
            field_dict["attached_archived"] = attached_archived

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        disposition = d.pop("disposition")

        def _parse_relationship_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        relationship_id = _parse_relationship_id(d.pop("relationship_id", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_attached_archived(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        attached_archived = _parse_attached_archived(d.pop("attached_archived", UNSET))

        relationship_observation_result = cls(
            disposition=disposition,
            relationship_id=relationship_id,
            reason=reason,
            attached_archived=attached_archived,
        )

        relationship_observation_result.additional_properties = d
        return relationship_observation_result

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
