from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.relationship_observation_item_op import RelationshipObservationItemOp
from ..types import UNSET, Unset

T = TypeVar("T", bound="RelationshipObservationItem")


@_attrs_define
class RelationshipObservationItem:
    """One relationship claim in an observation batch (#301).

    Identity = (from_pm_assignment_id, to_pm_assignment_id, rel_type); valid_from /
    valid_until / notes are mutable payload with **full-replace** semantics — a
    refine writes the whole mutable set, so an omitted field is cleared to NULL.
    ``pm_relationship_id`` addresses an existing edge for id-scoped refine/retract
    (identity immutable); absent → natural-key observe (refine-or-create). ``op``:
    ``observe`` (default) refines/creates, ``retract`` archives the pm_relationship_id row.

        Attributes:
            from_pm_assignment_id (None | str | Unset):
            to_pm_assignment_id (None | str | Unset):
            rel_type (str | Unset):  Default: 'staff_of'.
            valid_from (datetime.date | None | Unset):
            valid_until (datetime.date | None | Unset):
            notes (None | str | Unset):
            op (RelationshipObservationItemOp | Unset):  Default: RelationshipObservationItemOp.OBSERVE.
            pm_relationship_id (None | str | Unset):
    """

    from_pm_assignment_id: None | str | Unset = UNSET
    to_pm_assignment_id: None | str | Unset = UNSET
    rel_type: str | Unset = "staff_of"
    valid_from: datetime.date | None | Unset = UNSET
    valid_until: datetime.date | None | Unset = UNSET
    notes: None | str | Unset = UNSET
    op: RelationshipObservationItemOp | Unset = RelationshipObservationItemOp.OBSERVE
    pm_relationship_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from_pm_assignment_id: None | str | Unset
        if isinstance(self.from_pm_assignment_id, Unset):
            from_pm_assignment_id = UNSET
        else:
            from_pm_assignment_id = self.from_pm_assignment_id

        to_pm_assignment_id: None | str | Unset
        if isinstance(self.to_pm_assignment_id, Unset):
            to_pm_assignment_id = UNSET
        else:
            to_pm_assignment_id = self.to_pm_assignment_id

        rel_type = self.rel_type

        valid_from: None | str | Unset
        if isinstance(self.valid_from, Unset):
            valid_from = UNSET
        elif isinstance(self.valid_from, datetime.date):
            valid_from = self.valid_from.isoformat()
        else:
            valid_from = self.valid_from

        valid_until: None | str | Unset
        if isinstance(self.valid_until, Unset):
            valid_until = UNSET
        elif isinstance(self.valid_until, datetime.date):
            valid_until = self.valid_until.isoformat()
        else:
            valid_until = self.valid_until

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

        op: str | Unset = UNSET
        if not isinstance(self.op, Unset):
            op = self.op.value

        pm_relationship_id: None | str | Unset
        if isinstance(self.pm_relationship_id, Unset):
            pm_relationship_id = UNSET
        else:
            pm_relationship_id = self.pm_relationship_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if from_pm_assignment_id is not UNSET:
            field_dict["from_pm_assignment_id"] = from_pm_assignment_id
        if to_pm_assignment_id is not UNSET:
            field_dict["to_pm_assignment_id"] = to_pm_assignment_id
        if rel_type is not UNSET:
            field_dict["rel_type"] = rel_type
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until
        if notes is not UNSET:
            field_dict["notes"] = notes
        if op is not UNSET:
            field_dict["op"] = op
        if pm_relationship_id is not UNSET:
            field_dict["pm_relationship_id"] = pm_relationship_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_from_pm_assignment_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        from_pm_assignment_id = _parse_from_pm_assignment_id(d.pop("from_pm_assignment_id", UNSET))

        def _parse_to_pm_assignment_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        to_pm_assignment_id = _parse_to_pm_assignment_id(d.pop("to_pm_assignment_id", UNSET))

        rel_type = d.pop("rel_type", UNSET)

        def _parse_valid_from(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_from_type_0 = datetime.date.fromisoformat(data)

                return valid_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_from = _parse_valid_from(d.pop("valid_from", UNSET))

        def _parse_valid_until(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_until_type_0 = datetime.date.fromisoformat(data)

                return valid_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_until = _parse_valid_until(d.pop("valid_until", UNSET))

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

        _op = d.pop("op", UNSET)
        op: RelationshipObservationItemOp | Unset
        if isinstance(_op, Unset):
            op = UNSET
        else:
            op = RelationshipObservationItemOp(_op)

        def _parse_pm_relationship_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pm_relationship_id = _parse_pm_relationship_id(d.pop("pm_relationship_id", UNSET))

        relationship_observation_item = cls(
            from_pm_assignment_id=from_pm_assignment_id,
            to_pm_assignment_id=to_pm_assignment_id,
            rel_type=rel_type,
            valid_from=valid_from,
            valid_until=valid_until,
            notes=notes,
            op=op,
            pm_relationship_id=pm_relationship_id,
        )

        relationship_observation_item.additional_properties = d
        return relationship_observation_item

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
