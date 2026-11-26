"""#882: every observation refusal carries a stable ``ObservationReasonCode``."""

import pytest

from src.core.citations import CitationRejectReason
from src.core.observation import EventRejectReason, ObservationRejected
from src.core.person_name_observations import NameRejectReason, name_reason_code
from src.core.refusals import ObservationReasonCode, Refusal


def test_rejected_carries_its_code_and_keeps_the_text():
    exc = ObservationRejected(ObservationReasonCode.PROVENANCE_CONFLICT, "source_key_mismatch")
    assert exc.code is ObservationReasonCode.PROVENANCE_CONFLICT
    assert exc.detail == "source_key_mismatch"


def test_rejected_text_defaults_to_the_code():
    exc = ObservationRejected(ObservationReasonCode.PARENT_CYCLE)
    assert exc.detail == "parent_cycle"


def test_rejected_refuses_a_bare_string_code():
    with pytest.raises(TypeError):
        ObservationRejected("parent_cycle")  # type: ignore[arg-type]


def test_a_refusal_reads_as_its_text_and_carries_its_code():
    r = Refusal(ObservationReasonCode.PM_ID_NOT_FOUND, "pm_id_not_found: 'x'")
    assert r == "pm_id_not_found: 'x'"
    assert r.code is ObservationReasonCode.PM_ID_NOT_FOUND


def test_rejected_of_a_refusal_keeps_code_and_text():
    exc = ObservationRejected.of(Refusal(ObservationReasonCode.TITLE_REQUIRED))
    assert (exc.code, exc.detail) == (ObservationReasonCode.TITLE_REQUIRED, "title_required")


def test_rejected_of_nothing_is_invalid():
    """A resolver that answered REJECTED with no reason is a bug, not a 500."""
    exc = ObservationRejected.of(None)
    assert (exc.code, exc.detail) == (ObservationReasonCode.INVALID, "rejected")


@pytest.mark.parametrize("enum", [EventRejectReason, CitationRejectReason])
def test_embedded_item_slugs_are_codes(enum):
    """An embedded event or citation refusal re-raises its slug as the code."""
    assert {m.value for m in enum} <= {c.value for c in ObservationReasonCode}


def test_every_name_refusal_maps_to_a_code():
    assert name_reason_code(NameRejectReason.PROVENANCE_CONFLICT) is (
        ObservationReasonCode.PROVENANCE_CONFLICT
    )
    for reason in NameRejectReason:
        assert isinstance(name_reason_code(reason), ObservationReasonCode)


def test_one_name_for_the_provenance_gate():
    values = {c.value for c in ObservationReasonCode}
    assert "provenance_conflict" in values
    assert not {"source_key_mismatch", "name_provenance_conflict"} & values
