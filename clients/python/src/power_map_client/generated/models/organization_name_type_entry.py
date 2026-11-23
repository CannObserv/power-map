from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrganizationNameTypeEntry")


@_attrs_define
class OrganizationNameTypeEntry:
    """An organization name type's reference entry (#857).

    Attributes:
        slug (str):
        label (str):
        meaning (str):
        use_when (str):
        not_for (str):
        example (str):
    """

    slug: str
    label: str
    meaning: str
    use_when: str
    not_for: str
    example: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        label = self.label

        meaning = self.meaning

        use_when = self.use_when

        not_for = self.not_for

        example = self.example

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "slug": slug,
                "label": label,
                "meaning": meaning,
                "use_when": use_when,
                "not_for": not_for,
                "example": example,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        slug = d.pop("slug")

        label = d.pop("label")

        meaning = d.pop("meaning")

        use_when = d.pop("use_when")

        not_for = d.pop("not_for")

        example = d.pop("example")

        organization_name_type_entry = cls(
            slug=slug,
            label=label,
            meaning=meaning,
            use_when=use_when,
            not_for=not_for,
            example=example,
        )

        organization_name_type_entry.additional_properties = d
        return organization_name_type_entry

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
