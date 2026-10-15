from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.org_batch_get_request_etags import OrgBatchGetRequestEtags


T = TypeVar("T", bound="OrgBatchGetRequest")


@_attrs_define
class OrgBatchGetRequest:
    """Request body for POST /api/v1/orgs/batch-get (#654).

    Many ``getOrg`` reads in one call and one read-bucket token. Duplicate ids
    are deduped, first occurrence wins (the ``verify`` precedent). The cap is
    on the raw list, before the dedupe.

        Attributes:
            ids (list[str]): Org ids to read, 1–100. A string that is not an org id is `not_found`. Text Postgres cannot
                store (NUL, lone surrogates) is handled by #663's rule.
            etags (OrgBatchGetRequestEtags | Unset): Per-id `If-None-Match` value. An id whose ETag still matches answers
                `not_modified`. Compared exactly as `getOrg` compares the header, so either endpoint's ETag revalidates on the
                other. Keys not in `ids` are ignored.
    """

    ids: list[str]
    etags: OrgBatchGetRequestEtags | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ids = self.ids

        etags: dict[str, Any] | Unset = UNSET
        if not isinstance(self.etags, Unset):
            etags = self.etags.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ids": ids,
            }
        )
        if etags is not UNSET:
            field_dict["etags"] = etags

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.org_batch_get_request_etags import (
            OrgBatchGetRequestEtags,  # noqa: PLC0415
        )

        d = dict(src_dict)
        ids = cast(list[str], d.pop("ids"))

        _etags = d.pop("etags", UNSET)
        etags: OrgBatchGetRequestEtags | Unset
        if isinstance(_etags, Unset):
            etags = UNSET
        else:
            etags = OrgBatchGetRequestEtags.from_dict(_etags)

        org_batch_get_request = cls(
            ids=ids,
            etags=etags,
        )

        org_batch_get_request.additional_properties = d
        return org_batch_get_request

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
