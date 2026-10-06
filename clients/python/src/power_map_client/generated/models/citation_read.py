from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CitationRead")


@_attrs_define
class CitationRead:
    """A citation as returned by the read endpoint (#319).

    Attributes:
        id (str):
        entity_type (str):
        entity_id (str):
        created_at (datetime.datetime):
        updated_at (datetime.datetime):
        field_name (None | str | Unset):
        url (None | str | Unset):
        title (None | str | Unset):
        excerpt (None | str | Unset):
        accessed_at (datetime.datetime | None | Unset):
        archived_at (datetime.datetime | None | Unset):
    """

    id: str
    entity_type: str
    entity_id: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    field_name: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    excerpt: None | str | Unset = UNSET
    accessed_at: datetime.datetime | None | Unset = UNSET
    archived_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        entity_type = self.entity_type

        entity_id = self.entity_id

        created_at = self.created_at.isoformat()

        updated_at = self.updated_at.isoformat()

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

        archived_at: None | str | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        elif isinstance(self.archived_at, datetime.datetime):
            archived_at = self.archived_at.isoformat()
        else:
            archived_at = self.archived_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
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
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        entity_type = d.pop("entity_type")

        entity_id = d.pop("entity_id")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

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

        def _parse_archived_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                archived_at_type_0 = datetime.datetime.fromisoformat(data)

                return archived_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        citation_read = cls(
            id=id,
            entity_type=entity_type,
            entity_id=entity_id,
            created_at=created_at,
            updated_at=updated_at,
            field_name=field_name,
            url=url,
            title=title,
            excerpt=excerpt,
            accessed_at=accessed_at,
            archived_at=archived_at,
        )

        citation_read.additional_properties = d
        return citation_read

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
