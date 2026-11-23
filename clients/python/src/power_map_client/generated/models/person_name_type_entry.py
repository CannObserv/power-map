from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PersonNameTypeEntry")


@_attrs_define
class PersonNameTypeEntry:
    """A person name type's reference entry, with how PM treats it (#857).

    Read ``presentable`` before showing a name: a ``misspelling`` is ``false`` —
    a search key, never an "also known as". ``display_rank`` is the place on the
    display ladder (lower wins; ``null`` = never displayed unasked);
    ``auto_promote`` = may become the displayed name with no hint;
    ``never_canonical`` = an ``is_canonical`` hint on it is ignored;
    ``landing_visibility`` = what the row is stored as when sent ``public``;
    ``renders_row`` = a rendering of another name row (``reading_of_id``);
    ``merge_distinct`` = a merge keeps it beside same-text names of other types;
    ``decomposable`` = structured parts apply.

        Attributes:
            slug (str):
            label (str):
            meaning (str):
            use_when (str):
            not_for (str):
            example (str):
            display_rank (int | None):
            auto_promote (bool):
            never_canonical (bool):
            landing_visibility (str):
            renders_row (bool):
            merge_distinct (bool):
            decomposable (bool):
            presentable (bool):
    """

    slug: str
    label: str
    meaning: str
    use_when: str
    not_for: str
    example: str
    display_rank: int | None
    auto_promote: bool
    never_canonical: bool
    landing_visibility: str
    renders_row: bool
    merge_distinct: bool
    decomposable: bool
    presentable: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        label = self.label

        meaning = self.meaning

        use_when = self.use_when

        not_for = self.not_for

        example = self.example

        display_rank: int | None
        display_rank = self.display_rank

        auto_promote = self.auto_promote

        never_canonical = self.never_canonical

        landing_visibility = self.landing_visibility

        renders_row = self.renders_row

        merge_distinct = self.merge_distinct

        decomposable = self.decomposable

        presentable = self.presentable

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "slug": slug,
                "label": label,
                "meaning": meaning,
                "use_when": use_when,
                "not_for": not_for,
                "example": example,
                "display_rank": display_rank,
                "auto_promote": auto_promote,
                "never_canonical": never_canonical,
                "landing_visibility": landing_visibility,
                "renders_row": renders_row,
                "merge_distinct": merge_distinct,
                "decomposable": decomposable,
                "presentable": presentable,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        slug = d.pop("slug")

        label = d.pop("label")

        meaning = d.pop("meaning")

        use_when = d.pop("use_when")

        not_for = d.pop("not_for")

        example = d.pop("example")

        def _parse_display_rank(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        display_rank = _parse_display_rank(d.pop("display_rank"))

        auto_promote = d.pop("auto_promote")

        never_canonical = d.pop("never_canonical")

        landing_visibility = d.pop("landing_visibility")

        renders_row = d.pop("renders_row")

        merge_distinct = d.pop("merge_distinct")

        decomposable = d.pop("decomposable")

        presentable = d.pop("presentable")

        person_name_type_entry = cls(
            slug=slug,
            label=label,
            meaning=meaning,
            use_when=use_when,
            not_for=not_for,
            example=example,
            display_rank=display_rank,
            auto_promote=auto_promote,
            never_canonical=never_canonical,
            landing_visibility=landing_visibility,
            renders_row=renders_row,
            merge_distinct=merge_distinct,
            decomposable=decomposable,
            presentable=presentable,
        )

        person_name_type_entry.additional_properties = d
        return person_name_type_entry

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
