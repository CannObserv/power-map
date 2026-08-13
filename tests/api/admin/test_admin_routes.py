"""Guards on the shared admin route enumeration (GH #426).

Every entity detail template branches on ``archived_at`` — the archived half
carries the Unarchive control and the Danger Zone "Delete permanently" control.
``seed_admin_fixtures`` used to seed only active rows, so neither a11y tier
ever rendered that branch. These unit guards pin the fix: each entity detail
route must be swept a second time with an archived sibling filled in, and the
archived cases must stay wired into ``ADMIN_GET_PATHS`` (both tiers
parametrize off it, so presence there is what makes the branch swept).
"""

from tests.api.admin.admin_routes import ADMIN_GET_PATHS, ARCHIVED_DETAIL_PATHS

# The five entity detail pages whose templates branch on archived_at (#426):
# people, jurisdictions, role_assignments, roles detail.html plus the org
# detail's _active_toggle partial.
_DETAIL_ROUTES = {
    "/admin/jurisdictions/{jurisdiction_id}/",
    "/admin/orgs/{org_id}/",
    "/admin/people/{person_id}/",
    "/admin/role-assignments/{ra_id}/",
    "/admin/roles/{role_id}/",
}


def test_every_entity_detail_route_has_an_archived_sweep_case():
    """Each detail route is targeted by exactly one archived pseudo-path."""
    assert set(ARCHIVED_DETAIL_PATHS.values()) == _DETAIL_ROUTES


def test_archived_cases_are_in_the_enumeration_both_tiers_consume():
    """Both a11y tiers parametrize off ADMIN_GET_PATHS; an archived case only
    gets swept if it is *in* that list, alongside its active counterpart."""
    for pseudo, real in ARCHIVED_DETAIL_PATHS.items():
        assert pseudo in ADMIN_GET_PATHS, f"archived case {pseudo} missing from enumeration"
        assert real in ADMIN_GET_PATHS, f"active counterpart {real} missing from enumeration"


def test_archived_cases_fill_from_distinct_archived_params():
    """The pseudo-paths must use ``archived_*`` param names, so param_values
    fills them from the archived seeds rather than re-sweeping the active row."""
    for pseudo, real in ARCHIVED_DETAIL_PATHS.items():
        assert pseudo != real
        assert "{archived_" in pseudo, f"{pseudo} does not fill from an archived_* param"
