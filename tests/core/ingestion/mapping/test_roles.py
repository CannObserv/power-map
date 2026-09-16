"""The roles models (#529 step 6).

`desired_roles` is one row per published role, keyed by its `entity_id`, carrying
the producer ids and PM slugs the applier resolves — never PM ids, which a create
does not have. A create PM's own guards would refuse (#273/#302) is dropped here
rather than planned, and named by `role_create_pm_would_refuse`; an anchored
role is never dropped, since absence is what archives it. `desired_role_titles`
carries the one column the producer owns, with a curator's pin winning by
presence (#498).
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    DEFAULT_OVERLAY,
    MR1,
    O1,
    O2,
    R1,
    R2,
    R3,
    R4,
    crosswalk_row,
    fixture_csv,
    overlay_row,
)

ROLES, TITLES = "desired_roles", "desired_role_titles"


def _by_producer(built, model):
    return {r[1]: r for r in built.rows(model)}


def test_each_published_role_is_one_row_keyed_by_its_entity_id(build):
    rows = _by_producer(build(), ROLES)

    assert rows[R1] == (MR1, R1, O1, "committee_member", None, None, "Member")
    assert rows[R3] == (
        None,
        R3,
        O2,
        "state_senator",
        "usa-wa-ld-34",
        None,
        "Washington State Senator, LD-34",
    )


def test_a_role_whose_anchor_left_scope_is_neither_diffed_nor_created(build):
    """Its PM row is archived: the applier must not touch it, nor mint a twin."""
    crosswalk = [r for r in DEFAULT_CROSSWALK if r[3] != R2]
    crosswalk.append(crosswalk_row(R2, "01SXOUT0000000000000000000A", "archived", kind="role"))

    built = build(crosswalk=tuple(crosswalk))

    assert R2 not in _by_producer(built, ROLES)


def test_a_create_pms_guards_would_refuse_is_dropped_and_named(build):
    """#273: a districted seat of a per-position office needs its position, and the
    trigger would refuse the INSERT — so the create is never planned."""
    built = build()

    assert R4 not in _by_producer(built, ROLES)
    assert "role_create_pm_would_refuse" in built.warnings


def test_an_anchored_role_is_kept_whatever_its_qualifier(build):
    """Dropping it would read as absence, which archives a live role."""
    anchored = "01SR4000000000000000000000A"
    crosswalk = [*DEFAULT_CROSSWALK, crosswalk_row(R4, anchored, "live", kind="role")]

    rows = _by_producer(build(crosswalk=tuple(crosswalk)), ROLES)

    assert rows[R4][0] == anchored


def test_the_title_follows_the_producer(build):
    assert _by_producer(build(), TITLES)[R1] == (MR1, R1, "Member")


def test_a_pinned_title_wins(build):
    overlay = [*DEFAULT_OVERLAY, overlay_row("role", MR1, "title", "Curated Member")]

    assert _by_producer(build(overlay=tuple(overlay)), TITLES)[R1][2] == "Curated Member"


def test_a_null_pin_asserts_no_title(build):
    """PM holds a title the producer should not overwrite; a null pin says so."""
    overlay = [*DEFAULT_OVERLAY, overlay_row("role", MR1, "title", None)]

    assert _by_producer(build(overlay=tuple(overlay)), TITLES)[R1][2] is None


def test_role_pins_are_in_the_overlay_vocabulary(build):
    overlay = [*DEFAULT_OVERLAY, overlay_row("role", MR1, "title", "Curated Member")]

    assert "overlay_field_unmapped" not in build(overlay=tuple(overlay)).warnings


def test_a_role_type_pm_does_not_carry_is_kept_for_the_applier_to_refuse(build):
    """The applier's lookup names it (#302); the model cannot tell a new type from a
    typo, and dropping an anchored role would archive it."""
    line = ",".join(
        [R3, "seat:senate:ld-34", "at_large_senator", "Senator", "chamber-senate", "34"]
        + ["usa_wa_senate", O2, "34", ""]
    )
    built = build(datasets={"roles": fixture_csv("roles", drop=R3, add=[line])})

    assert _by_producer(built, ROLES)[R3][3] == "at_large_senator"
