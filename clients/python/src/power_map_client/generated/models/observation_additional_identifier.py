from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationAdditionalIdentifier")


@_attrs_define
class ObservationAdditionalIdentifier:
    """An additional identifier claim to attach to the resolved entity.

    ``replaces`` (#848) turns the attach into a swap: the entity's value
    ``replaces`` is removed and ``identifier_value`` is held instead, in the
    observation's one transaction. Only the one-holder types take it — the four
    ``*_cannabis_observer`` and the two ``*_co_operator`` ones
    (``replaces_unsupported`` otherwise). An entity holding neither value
    rejects the observation ``replaces_not_held``; one already holding the new
    value and not the old is a no-op, so a replace can be re-sent.

        Attributes:
            identifier_type_slug (str):
            identifier_value (str):
            replaces (None | str | Unset):
    """

    identifier_type_slug: str
    identifier_value: str
    replaces: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identifier_type_slug = self.identifier_type_slug

        identifier_value = self.identifier_value

        replaces: None | str | Unset
        if isinstance(self.replaces, Unset):
            replaces = UNSET
        else:
            replaces = self.replaces

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "identifier_type_slug": identifier_type_slug,
                "identifier_value": identifier_value,
            }
        )
        if replaces is not UNSET:
            field_dict["replaces"] = replaces

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        identifier_type_slug = d.pop("identifier_type_slug")

        identifier_value = d.pop("identifier_value")

        def _parse_replaces(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        replaces = _parse_replaces(d.pop("replaces", UNSET))

        observation_additional_identifier = cls(
            identifier_type_slug=identifier_type_slug,
            identifier_value=identifier_value,
            replaces=replaces,
        )

        observation_additional_identifier.additional_properties = d
        return observation_additional_identifier

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
