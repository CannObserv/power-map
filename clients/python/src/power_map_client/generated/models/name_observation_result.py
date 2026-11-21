from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.citation_observation_result import CitationObservationResult


T = TypeVar("T", bound="NameObservationResult")


@_attrs_define
class NameObservationResult:
    """Per-name outcome on a people observation (#845), in ``names`` request order.

    ``disposition``: ``new`` (the name was added), ``auto-attached`` (the person
    already held it), ``retracted`` (``op="retract"``), or ``not-public``.

    ``name_id`` addresses the name row — the ``person_name`` entity of the
    citations API. It is null on ``not-public``, and on a ``retracted`` that
    found no public row of that identity to delete (nothing was removed).

    ``not-public``: the claim was accepted, but the name is not public — a
    ``deadname`` (always stored ``legal_only``), or a name a curator has hidden,
    which a re-send does not republish. No id and no citation results. For a
    ``deadname`` it does not say whether the name was already held; for any
    other type it means the person already holds the name, non-publicly.

        Attributes:
            disposition (str):
            name_id (None | str | Unset):
            citations (list[CitationObservationResult] | None | Unset):
    """

    disposition: str
    name_id: None | str | Unset = UNSET
    citations: list[CitationObservationResult] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        disposition = self.disposition

        name_id: None | str | Unset
        if isinstance(self.name_id, Unset):
            name_id = UNSET
        else:
            name_id = self.name_id

        citations: list[dict[str, Any]] | None | Unset
        if isinstance(self.citations, Unset):
            citations = UNSET
        elif isinstance(self.citations, list):
            citations = []
            for citations_type_0_item_data in self.citations:
                citations_type_0_item = citations_type_0_item_data.to_dict()
                citations.append(citations_type_0_item)

        else:
            citations = self.citations

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "disposition": disposition,
            }
        )
        if name_id is not UNSET:
            field_dict["name_id"] = name_id
        if citations is not UNSET:
            field_dict["citations"] = citations

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.citation_observation_result import CitationObservationResult  # noqa: PLC0415

        d = dict(src_dict)
        disposition = d.pop("disposition")

        def _parse_name_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name_id = _parse_name_id(d.pop("name_id", UNSET))

        def _parse_citations(data: object) -> list[CitationObservationResult] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                citations_type_0 = []
                _citations_type_0 = data
                for citations_type_0_item_data in _citations_type_0:
                    citations_type_0_item = CitationObservationResult.from_dict(
                        citations_type_0_item_data
                    )

                    citations_type_0.append(citations_type_0_item)

                return citations_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[CitationObservationResult] | None | Unset, data)

        citations = _parse_citations(d.pop("citations", UNSET))

        name_observation_result = cls(
            disposition=disposition,
            name_id=name_id,
            citations=citations,
        )

        name_observation_result.additional_properties = d
        return name_observation_result

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
