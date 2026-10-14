"""The `active` hint (#592): PM keeps `organizations.active`; a warn test names drift.

The applier never writes `active`. For committees `dissolved` already carries
it, and usa-wa flips every committee inactive at a biennium rollover — so the
hint reads only orgs with no `last_biennium` (Joint/Other, parties, chambers),
the class where usa-wa's flag says something PM's dissolved rule does not.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_PM_ORGS,
    MO4,
    MO6,
    NOW,
    O6,
    pm_org_row,
)

HINT = "org_active_divergence"


def _with(pm_id, active, *, archived_at=None):
    return [r for r in DEFAULT_PM_ORGS if r[0] != pm_id] + [
        pm_org_row(pm_id, active, archived_at=archived_at)
    ]


def test_staging_carries_usa_was_active_flag(build):
    b = build()

    flags = {r[0]: r[-1] for r in b.rows("stg_usa_wa__organizations")}
    assert flags[O6] is True
    assert b.columns("stg_usa_wa__organizations")[-1] == "active"


def test_agreement_raises_nothing(build):
    assert HINT not in build().warnings


def test_a_joint_committee_pm_holds_active_that_usa_wa_does_not_is_named(build):
    """GFIE (#591): usa-wa had it inactive; PM had it live until a person looked."""
    b = build(pm_orgs=_with(MO6, False))

    assert HINT in b.warnings


def test_a_committee_mismatch_is_left_to_dissolved(build):
    """The rollover flips every committee in usa-wa at once: no hint for them."""
    b = build(pm_orgs=_with(MO4, False))

    assert HINT not in b.warnings


def test_an_org_pm_archived_is_not_named(build):
    b = build(pm_orgs=_with(MO6, False, archived_at=NOW))

    assert HINT not in b.warnings
