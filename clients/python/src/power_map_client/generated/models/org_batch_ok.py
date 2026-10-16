from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.org_detail import OrgDetail


T = TypeVar("T", bound="OrgBatchOk")


@_attrs_define
class OrgBatchOk:
    """``getOrg``'s ``200``: the org (archived included) and its ETag.

    Attributes:
        outcome (Literal['ok']):
        id (str):
        etag (str):
        org (OrgDetail): Full org record including name variants, acronyms, identifiers, and affiliations.
    """

    outcome: Literal["ok"]
    id: str
    etag: str
    org: OrgDetail
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome

        id = self.id

        etag = self.etag

        org = self.org.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "outcome": outcome,
                "id": id,
                "etag": etag,
                "org": org,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.org_detail import OrgDetail  # noqa: PLC0415

        d = dict(src_dict)
        outcome = cast(Literal["ok"], d.pop("outcome"))
        if outcome != "ok":
            raise ValueError(f"outcome must match const 'ok', got '{outcome}'")

        id = d.pop("id")

        etag = d.pop("etag")

        org = OrgDetail.from_dict(d.pop("org"))

        org_batch_ok = cls(
            outcome=outcome,
            id=id,
            etag=etag,
            org=org,
        )

        org_batch_ok.additional_properties = d
        return org_batch_ok

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
