from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ChangeMeta")


@_attrs_define
class ChangeMeta:
    """Pagination metadata for the change feed.

    Attributes:
        limit (int):
        count (int):
        has_more (bool):
        next_after (int):
        min_seq (int | None | Unset): Oldest outbox seq_id still retained — the prune horizon (#388). null when the
            outbox is empty. Global, not subscription-scoped: pruning is a global changed_at delete, so this is the single
            id below which any event may already be gone. On resume, if your persisted `after` is below `min_seq - 1`,
            events may have been pruned before you read them — full-reconcile against the read endpoints.
    """

    limit: int
    count: int
    has_more: bool
    next_after: int
    min_seq: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        limit = self.limit

        count = self.count

        has_more = self.has_more

        next_after = self.next_after

        min_seq: int | None | Unset
        if isinstance(self.min_seq, Unset):
            min_seq = UNSET
        else:
            min_seq = self.min_seq

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "limit": limit,
                "count": count,
                "has_more": has_more,
                "next_after": next_after,
            }
        )
        if min_seq is not UNSET:
            field_dict["min_seq"] = min_seq

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        limit = d.pop("limit")

        count = d.pop("count")

        has_more = d.pop("has_more")

        next_after = d.pop("next_after")

        def _parse_min_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        min_seq = _parse_min_seq(d.pop("min_seq", UNSET))

        change_meta = cls(
            limit=limit,
            count=count,
            has_more=has_more,
            next_after=next_after,
            min_seq=min_seq,
        )

        change_meta.additional_properties = d
        return change_meta

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
