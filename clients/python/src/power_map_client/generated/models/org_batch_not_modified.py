from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrgBatchNotModified")


@_attrs_define
class OrgBatchNotModified:
    """``getOrg``'s ``304``: the caller's ETag for this id still matches.

    Attributes:
        outcome (Literal['not_modified']):
        id (str):
        etag (str):
    """

    outcome: Literal["not_modified"]
    id: str
    etag: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome

        id = self.id

        etag = self.etag

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "outcome": outcome,
                "id": id,
                "etag": etag,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        outcome = cast(Literal["not_modified"], d.pop("outcome"))
        if outcome != "not_modified":
            raise ValueError(f"outcome must match const 'not_modified', got '{outcome}'")

        id = d.pop("id")

        etag = d.pop("etag")

        org_batch_not_modified = cls(
            outcome=outcome,
            id=id,
            etag=etag,
        )

        org_batch_not_modified.additional_properties = d
        return org_batch_not_modified

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
