from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.org_batch_gone import OrgBatchGone
    from ..models.org_batch_merged import OrgBatchMerged
    from ..models.org_batch_not_found import OrgBatchNotFound
    from ..models.org_batch_not_modified import OrgBatchNotModified
    from ..models.org_batch_ok import OrgBatchOk


T = TypeVar("T", bound="OrgBatchGetResponse")


@_attrs_define
class OrgBatchGetResponse:
    """Response for POST /api/v1/orgs/batch-get: one result per distinct id, in
    request order. ``outcome`` selects the variant; a new outcome would be a
    breaking change for a generated client.

        Attributes:
            results (list[OrgBatchGone | OrgBatchMerged | OrgBatchNotFound | OrgBatchNotModified | OrgBatchOk]):
    """

    results: list[
        OrgBatchGone | OrgBatchMerged | OrgBatchNotFound | OrgBatchNotModified | OrgBatchOk
    ]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.org_batch_gone import OrgBatchGone  # noqa: PLC0415
        from ..models.org_batch_merged import OrgBatchMerged  # noqa: PLC0415
        from ..models.org_batch_not_modified import OrgBatchNotModified  # noqa: PLC0415
        from ..models.org_batch_ok import OrgBatchOk  # noqa: PLC0415

        results = []
        for results_item_data in self.results:
            results_item: dict[str, Any]
            if isinstance(results_item_data, OrgBatchOk):
                results_item = results_item_data.to_dict()
            elif isinstance(results_item_data, OrgBatchNotModified):
                results_item = results_item_data.to_dict()
            elif isinstance(results_item_data, OrgBatchMerged):
                results_item = results_item_data.to_dict()
            elif isinstance(results_item_data, OrgBatchGone):
                results_item = results_item_data.to_dict()
            else:
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
        from ..models.org_batch_gone import OrgBatchGone  # noqa: PLC0415
        from ..models.org_batch_merged import OrgBatchMerged  # noqa: PLC0415
        from ..models.org_batch_not_found import OrgBatchNotFound  # noqa: PLC0415
        from ..models.org_batch_not_modified import OrgBatchNotModified  # noqa: PLC0415
        from ..models.org_batch_ok import OrgBatchOk  # noqa: PLC0415

        d = dict(src_dict)
        results = []
        _results = d.pop("results")
        for results_item_data in _results:

            def _parse_results_item(
                data: object,
            ) -> (
                OrgBatchGone | OrgBatchMerged | OrgBatchNotFound | OrgBatchNotModified | OrgBatchOk
            ):
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    results_item_type_0 = OrgBatchOk.from_dict(data)

                    return results_item_type_0
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    results_item_type_1 = OrgBatchNotModified.from_dict(data)

                    return results_item_type_1
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    results_item_type_2 = OrgBatchMerged.from_dict(data)

                    return results_item_type_2
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                try:
                    if not isinstance(data, dict):
                        raise TypeError()
                    results_item_type_3 = OrgBatchGone.from_dict(data)

                    return results_item_type_3
                except (TypeError, ValueError, AttributeError, KeyError):
                    pass
                if not isinstance(data, dict):
                    raise TypeError()
                results_item_type_4 = OrgBatchNotFound.from_dict(data)

                return results_item_type_4

            results_item = _parse_results_item(results_item_data)

            results.append(results_item)

        org_batch_get_response = cls(
            results=results,
        )

        org_batch_get_response.additional_properties = d
        return org_batch_get_response

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
