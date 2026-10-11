from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.identify_match import IdentifyMatch


T = TypeVar("T", bound="IdentifyBatchGroup")


@_attrs_define
class IdentifyBatchGroup:
    """Per-embedding result group — ``embedding_index`` is the position of the
    query embedding in the request's ``embeddings`` list; ``matches`` is what
    ``/identify`` returns for that embedding alone.

        Attributes:
            embedding_index (int):
            matches (list[IdentifyMatch]):
    """

    embedding_index: int
    matches: list[IdentifyMatch]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embedding_index = self.embedding_index

        matches = []
        for matches_item_data in self.matches:
            matches_item = matches_item_data.to_dict()
            matches.append(matches_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embedding_index": embedding_index,
                "matches": matches,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.identify_match import IdentifyMatch  # noqa: PLC0415

        d = dict(src_dict)
        embedding_index = d.pop("embedding_index")

        matches = []
        _matches = d.pop("matches")
        for matches_item_data in _matches:
            matches_item = IdentifyMatch.from_dict(matches_item_data)

            matches.append(matches_item)

        identify_batch_group = cls(
            embedding_index=embedding_index,
            matches=matches,
        )

        identify_batch_group.additional_properties = d
        return identify_batch_group

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
