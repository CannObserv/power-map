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
    R5,
    SEAT_ELECT,
    crosswalk_row,
    fixture_csv,
    overlay_row,
    seat_elect_role,
)

ROLES, TITLES = "desired_roles", "desired_role_titles"
UNKNOWN_TYPE = "published_role_type_unknown_to_pm"  # #883
UNBOUND = "not_null_stg_usa_wa__roles_entity_id"  # #883: the registrar's one-night lag
# More unbound roles than a seat-elect cycle can publish (WA: 98 House + 25
# Senate) is a registrar regression, not the lag: the build halts (#883 CR 1).
UNBOUND_HALT_ABOVE = 150

# Every test here builds the dbt project (#890: the commit hook routes by it).
pytestmark = pytest.mark.dbt


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


def test_a_role_out_of_row_scope_is_not_named_as_a_refused_create(build):
    """Its anchor resolved to no PM row, so the model never considers creating it —
    and the warn names what desired_roles dropped, not every row PM would refuse."""
    crosswalk = [*DEFAULT_CROSSWALK, crosswalk_row(R4, None, "missing", kind="role")]

    built = build(crosswalk=tuple(crosswalk))

    assert R4 not in _by_producer(built, ROLES)
    assert "role_create_pm_would_refuse" not in built.warnings


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
    assert UNKNOWN_TYPE in built.warnings


def test_every_type_the_fixture_publishes_is_in_pms_vocabulary(build):
    assert UNKNOWN_TYPE not in build().warnings


def test_an_anchored_role_whose_type_pm_lacks_is_named(build):
    """#883: identity is written once, so the applier never reads an anchored role's
    type again — the guard is the only word that it moved somewhere PM cannot follow."""
    roles = fixture_csv(
        "roles",
        replace={
            "committee-member-role:31640,committee_member": (
                "committee-member-role:31640,committee_clerk"
            )
        },
    )

    built = build(datasets={"roles": roles})

    assert _by_producer(built, ROLES)[R1][0] == MR1
    assert UNKNOWN_TYPE in built.warnings


def test_a_seat_elect_role_is_a_districted_create(build):
    """#883 (usa-wa#600): a member-elect's office is its seat's, held early."""
    built = build(datasets={"roles": fixture_csv("roles", add=[seat_elect_role()])})

    assert _by_producer(built, ROLES)[R5] == (
        None,
        R5,
        O2,
        "state_representative_elect",
        "usa-wa-ld-34",
        "Position 1",
        "Washington State Representative-elect, LD-34, Position 1",
    )
    assert UNKNOWN_TYPE not in built.warnings


def test_an_unbound_role_waits_a_night_without_failing_the_build(build):
    """#883: a brand-new role publishes with no entity_id; the next night's registrar
    binds it. Never anchored, so leaving it out archives nothing — and it is not a
    create PM would refuse, so the refusal warn stays quiet (R4 anchored here)."""
    crosswalk = [
        *DEFAULT_CROSSWALK,
        crosswalk_row(R4, "01SR4000000000000000000000A", "live", kind="role"),
    ]
    roles = fixture_csv("roles", add=[seat_elect_role("")])

    built = build(crosswalk=tuple(crosswalk), datasets={"roles": roles})

    assert UNBOUND in built.warnings
    assert None not in _by_producer(built, ROLES)
    assert None not in _by_producer(built, TITLES)
    assert "role_create_pm_would_refuse" not in built.warnings


def _unbound_roles(n: int) -> str:
    """The roles CSV with ``n`` unbound seat-elect roles added, each its own key."""
    lines = [seat_elect_role("").replace(SEAT_ELECT, f"{SEAT_ELECT}-{i}") for i in range(n)]
    return fixture_csv("roles", add=lines)


def test_a_seat_elect_cycle_of_unbound_roles_still_only_warns(build):
    """#883 CR 1: the most unbound roles one night's lag can explain is a warning."""
    built = build(datasets={"roles": _unbound_roles(UNBOUND_HALT_ABOVE)})

    assert UNBOUND in built.warnings


def test_more_unbound_roles_than_a_cycle_halts_the_build(build):
    """#883 CR 1: a registrar regression that unbinds every role must halt at build with
    its cause named, not reach the dry run as one archive per anchored role."""
    built = build(datasets={"roles": _unbound_roles(UNBOUND_HALT_ABOVE + 1)}, must_succeed=False)

    assert UNBOUND in built.failures
