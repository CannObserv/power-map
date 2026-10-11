from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="IdentifyBatchRequest")


@_attrs_define
class IdentifyBatchRequest:
    """Request body for POST /api/v1/people/identify-batch (#638).

    Open-set identify for N query embeddings in one call — collapses the
    per-centroid identify loop into one read-bucket token.  The cap matches
    verify-batch; with ``top_k`` clamped to 25 it bounds a call at 1,250
    matches.  Every field applies to the whole batch, so per-batch context
    hints (observo#235) belong beside them, never inside ``embeddings``.

        Attributes:
            model_id (str):
            embeddings (list[list[float]]):
            top_k (int | Unset):  Default: 5.
    """

    model_id: str
    embeddings: list[list[float]]
    top_k: int | Unset = 5
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        embeddings = []
        for embeddings_item_data in self.embeddings:
            embeddings_item = embeddings_item_data

            embeddings.append(embeddings_item)

        top_k = self.top_k

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "embeddings": embeddings,
            }
        )
        if top_k is not UNSET:
            field_dict["top_k"] = top_k

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_id = d.pop("model_id")

        embeddings = []
        _embeddings = d.pop("embeddings")
        for embeddings_item_data in _embeddings:
            embeddings_item = cast(list[float], embeddings_item_data)

            embeddings.append(embeddings_item)

        top_k = d.pop("top_k", UNSET)

        identify_batch_request = cls(
            model_id=model_id,
            embeddings=embeddings,
            top_k=top_k,
        )

        identify_batch_request.additional_properties = d
        return identify_batch_request

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
