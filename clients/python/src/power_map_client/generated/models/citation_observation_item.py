from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.citation_observation_item_op import CitationObservationItemOp
from ..types import UNSET, Unset

T = TypeVar("T", bound="CitationObservationItem")


@_attrs_define
class CitationObservationItem:
    """One citation claim in a citation observation batch (#319).

    Identity = (entity, field_name, url); title/excerpt/accessed_at are mutable
    payload with **full-replace** semantics — a refine writes the whole mutable set,
    so a field omitted from the claim is cleared to NULL (same model as event
    refine). Send the complete payload on every observe/refine. ``pm_citation_id``
    addresses an existing citation for id-scoped refine/retract; absent → natural-key
    observe (refine-or-create). ``op``: ``observe`` (default) refines/creates,
    ``retract`` archives the pm_citation_id row.

        Attributes:
            field_name (None | str | Unset):
            url (None | str | Unset):
            title (None | str | Unset):
            excerpt (None | str | Unset):
            accessed_at (datetime.datetime | None | Unset):
            op (CitationObservationItemOp | Unset):  Default: CitationObservationItemOp.OBSERVE.
            pm_citation_id (None | str | Unset):
    """

    field_name: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    excerpt: None | str | Unset = UNSET
    accessed_at: datetime.datetime | None | Unset = UNSET
    op: CitationObservationItemOp | Unset = CitationObservationItemOp.OBSERVE
    pm_citation_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        field_name: None | str | Unset
        if isinstance(self.field_name, Unset):
            field_name = UNSET
        else:
            field_name = self.field_name

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        excerpt: None | str | Unset
        if isinstance(self.excerpt, Unset):
            excerpt = UNSET
        else:
            excerpt = self.excerpt

        accessed_at: None | str | Unset
        if isinstance(self.accessed_at, Unset):
            accessed_at = UNSET
        elif isinstance(self.accessed_at, datetime.datetime):
            accessed_at = self.accessed_at.isoformat()
        else:
            accessed_at = self.accessed_at

        op: str | Unset = UNSET
        if not isinstance(self.op, Unset):
            op = self.op.value

        pm_citation_id: None | str | Unset
        if isinstance(self.pm_citation_id, Unset):
            pm_citation_id = UNSET
        else:
            pm_citation_id = self.pm_citation_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if field_name is not UNSET:
            field_dict["field_name"] = field_name
        if url is not UNSET:
            field_dict["url"] = url
        if title is not UNSET:
            field_dict["title"] = title
        if excerpt is not UNSET:
            field_dict["excerpt"] = excerpt
        if accessed_at is not UNSET:
            field_dict["accessed_at"] = accessed_at
        if op is not UNSET:
            field_dict["op"] = op
        if pm_citation_id is not UNSET:
            field_dict["pm_citation_id"] = pm_citation_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_field_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        field_name = _parse_field_name(d.pop("field_name", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        def _parse_excerpt(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        excerpt = _parse_excerpt(d.pop("excerpt", UNSET))

        def _parse_accessed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                accessed_at_type_0 = datetime.datetime.fromisoformat(data)

                return accessed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        accessed_at = _parse_accessed_at(d.pop("accessed_at", UNSET))

        _op = d.pop("op", UNSET)
        op: CitationObservationItemOp | Unset
        if isinstance(_op, Unset):
            op = UNSET
        else:
            op = CitationObservationItemOp(_op)

        def _parse_pm_citation_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pm_citation_id = _parse_pm_citation_id(d.pop("pm_citation_id", UNSET))

        citation_observation_item = cls(
            field_name=field_name,
            url=url,
            title=title,
            excerpt=excerpt,
            accessed_at=accessed_at,
            op=op,
            pm_citation_id=pm_citation_id,
        )

        citation_observation_item.additional_properties = d
        return citation_observation_item

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
