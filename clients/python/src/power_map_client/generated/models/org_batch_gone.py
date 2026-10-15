from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrgBatchGone")


@_attrs_define
class OrgBatchGone:
    """``getOrg``'s ``410`` with ``merged_into: null``: deleted, or merged down a
    chain that ends in a delete.

        Attributes:
            outcome (Literal['gone']):
            id (str):
            deleted_at (datetime.datetime):
    """

    outcome: Literal["gone"]
    id: str
    deleted_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome

        id = self.id

        deleted_at = self.deleted_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "outcome": outcome,
                "id": id,
                "deleted_at": deleted_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        outcome = cast(Literal["gone"], d.pop("outcome"))
        if outcome != "gone":
            raise ValueError(f"outcome must match const 'gone', got '{outcome}'")

        id = d.pop("id")

        deleted_at = datetime.datetime.fromisoformat(d.pop("deleted_at"))

        org_batch_gone = cls(
            outcome=outcome,
            id=id,
            deleted_at=deleted_at,
        )

        org_batch_gone.additional_properties = d
        return org_batch_gone

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
