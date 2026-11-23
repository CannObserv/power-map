"""How a slot's value reads to a curator, and what its line offers (#838).

Pure: no database. A slot's live and pinned values arrive as the text a pin
stores (`curation_overlay.pin_text`), and `read_as` turns that text into what a
curator reads. Then the line's template, rendered from a `SlotState`, in each
of its states: filled, empty, pinned, pinned NULL, pinned but no longer live.
"""

from datetime import UTC, datetime

import pytest
from fastapi.templating import Jinja2Templates

from src.api.admin.overlay_slots import SLOTS, SlotState, read_as
from src.core.curation_overlay import Pin, pin_text

_TEMPLATE = Jinja2Templates(directory="src/templates").get_template(
    "admin/shared/_overlay_slot.html"
)
BASE = "/admin/_overlay/organization/O1/dissolved_year/"


@pytest.mark.parametrize(
    ("field", "text", "reads"),
    [
        (("assignment", "is_current"), "True", "Current"),
        (("assignment", "is_current"), "False", "Former"),
        (("assignment", "membership_type"), "principal", "Principal"),
        (("assignment", "membership_type"), "ex_officio", "Ex officio"),
        (("assignment", "membership_type"), "substitute", "Substitute"),
        (("assignment", "membership_type"), "acting", "Acting"),
        (("organization", "dissolved_year"), "2019", "2019"),
        (("assignment", "end_date"), "2022-12-31", "2022-12-31"),
        (("organization", "acronym"), "ETT", "ETT"),
        (("organization", "acronym"), None, None),
    ],
)
def test_a_stored_value_reads_as_a_curator_says_it(field, text, reads):
    assert read_as(SLOTS[field], text) == reads


def test_a_live_bool_and_a_pinned_bool_read_alike():
    """The live column is a bool, the pin's TEXT holds 'False': one reading for both."""
    slot = SLOTS[("assignment", "is_current")]
    assert read_as(slot, pin_text(False)) == read_as(slot, "False") == "Former"


def test_no_slot_label_makes_a_pin_current_button():
    """CR 2: "Pin current ‹label›" is the wording #838 retired; a label "Current"
    would bring it back as "Pin current"."""
    assert all(s.label.lower() != "current" for s in SLOTS.values())


def test_only_the_membership_slot_has_no_editor():
    assert [k for k, s in SLOTS.items() if not s.editable] == [("assignment", "membership_type")]


def test_a_slot_state_cannot_omit_the_live_value():
    """CR 3: a default None would render a filled slot as empty, with no Pin."""
    with pytest.raises(TypeError):
        SlotState(slot=SLOTS[("role", "title")], pin=None, display=None, pinned_by=None)


# --- the line, rendered --------------------------------------------------------------


def _pin(value: str | None) -> Pin:
    return Pin(
        id="P1",
        entity_type="organization",
        entity_id="O1",
        field="dissolved_year",
        value=value,
        note=None,
        created_by=None,
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
        archived_at=None,
        archived_by=None,
    )


def _line(*, live: str | None, held: str | None = "unpinned", field=None) -> str:
    slot = SLOTS[field or ("organization", "dissolved_year")]
    pin = None if held == "unpinned" else _pin(held)
    state = SlotState(
        slot=slot,
        pin=pin,
        display=None if pin is None else read_as(slot, pin.value),
        pinned_by=None,
        live=live,
        live_display=read_as(slot, live),
    )
    return _TEMPLATE.render(state=state, variant="status", producer="usa-wa", base=BASE)


def test_a_filled_line_shows_the_value_and_a_pin_that_posts_it():
    html = _line(live="2019")

    assert "Dissolved year: <strong>2019</strong>. Maintained by usa-wa." in html
    assert "Editing it pins your value." in html
    assert f'hx-post="{BASE}pin/"' in html
    assert """hx-vals='{"expected": "2019"}'""" in html
    assert ">Pin dissolved year</button>" in html and "Pin current" not in html


def test_an_empty_line_says_none_and_offers_no_pin():
    html = _line(live=None)

    assert "Dissolved year: <em>none</em>. Maintained by usa-wa." in html
    assert "pin/" not in html and "<button" not in html


def test_a_slot_with_no_editor_does_not_say_editing_pins():
    html = _line(live="principal", field=("assignment", "membership_type"))

    assert "Membership: <strong>Principal</strong>." in html
    assert "Editing it" not in html


def test_a_pinned_line_shows_the_value_and_an_unpin_by_label():
    html = _line(live="2019", held="2019")

    assert 'class="badge badge--pinned"' in html
    assert "Dissolved year: <strong>2019</strong>. PM keeps it over usa-wa's value." in html
    assert ">Unpin dissolved year</button>" in html
    assert "PM's record reads" not in html


def test_a_null_pin_reads_none_kept_empty():
    html = _line(live=None, held=None)

    assert "Dissolved year: <em>none, kept empty</em>." in html
    assert "PM keeps it empty over usa-wa's value." in html


def test_a_pin_that_differs_from_the_live_value_says_so():
    html = _line(live="2020", held="2019")

    assert "PM's record now reads <strong>2020</strong>, not the pinned value." in html


def test_a_pinned_value_over_an_emptied_slot_says_none():
    html = _line(live=None, held="2019")

    assert "PM's record now reads <em>none</em>, not the pinned value." in html


def test_the_expected_value_is_attribute_safe():
    """A title with a quote must not break out of the single-quoted `hx-vals`."""
    html = _line(live="Chair's 'pro tem'", field=("role", "title"))

    assert 'hx-vals=\'{"expected": "Chair\\u0027s \\u0027pro tem\\u0027"}\'' in html
