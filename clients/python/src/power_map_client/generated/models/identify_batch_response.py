from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.identify_batch_group import IdentifyBatchGroup


T = TypeVar("T", bound="IdentifyBatchResponse")


@_attrs_define
class IdentifyBatchResponse:
    """Response envelope for POST /api/v1/people/identify-batch — one group per
    query embedding, in ``embeddings`` request order.

        Attributes:
            results (list[IdentifyBatchGroup]):
    """

    results: list[IdentifyBatchGroup]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        results = []
        for results_item_data in self.results:
            results_item = results_item_data.to_dict()
            results.append(results_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "results": results,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.identify_batch_group import IdentifyBatchGroup  # noqa: PLC0415

        d = dict(src_dict)
        results = []
        _results = d.pop("results")
        for results_item_data in _results:
            results_item = IdentifyBatchGroup.from_dict(results_item_data)

            results.append(results_item)

        identify_batch_response = cls(
            results=results,
        )

        identify_batch_response.additional_properties = d
        return identify_batch_response

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
