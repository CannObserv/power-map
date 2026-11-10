"""The default run turns every ``DeprecationWarning`` into an error (#789).

#786's strict ``--collect-only`` guards one file at startup. A deprecation
raised at another module's collection, at fixture setup or at test time got
past it: pytest 9.2's ``PytestRemovedIn10Warning`` on a class-scoped
instance-method fixture errored 3 tests at setup, and only a hand-run
``-W error::DeprecationWarning`` found it. Since nothing ran strict
routinely, the ini entry now makes every run strict, pre-commit and the CI gate
included. Under pytest 9.2 it applies in ``pytest_configure``, at collection
and at test time.

Later ``filterwarnings`` entries win, so an exemption goes after the
``error::`` line and names a module or a message. A bare
``ignore::DeprecationWarning`` would switch the rule off. Each guard reads the
running session's config, not the file, so it pins what pytest applied.
"""

import warnings

import pytest

STRICT = "error::DeprecationWarning"


def test_deprecations_are_errors_first(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("filterwarnings")[:1] == [STRICT]


def test_a_deprecation_at_test_time_raises() -> None:
    with pytest.raises(DeprecationWarning):
        warnings.warn("probe", DeprecationWarning, stacklevel=1)


def test_a_pytest_removal_warning_raises() -> None:
    """The #786 case: ``PytestRemovedIn10Warning`` is a ``DeprecationWarning``."""
    with pytest.raises(pytest.PytestRemovedIn10Warning):
        warnings.warn(pytest.PytestRemovedIn10Warning("probe"), stacklevel=1)


def _unscoped_ignores(entries: list[str]) -> list[str]:
    """``ignore`` entries that name neither a message nor a module.

    pytest's filter string is ``action:message:category:module:lineno``.
    """
    offenders = []
    for entry in entries:
        action, message, _category, module, *_ = (entry.split(":", 4) + [""] * 5)[:5]
        if action.strip() == "ignore" and not message.strip() and not module.strip():
            offenders.append(entry)
    return offenders


def test_the_scope_detector_is_live() -> None:
    assert _unscoped_ignores(["ignore::DeprecationWarning"]) == ["ignore::DeprecationWarning"]
    assert _unscoped_ignores(["ignore"]) == ["ignore"]
    assert _unscoped_ignores(["ignore:::"]) == ["ignore:::"]
    assert _unscoped_ignores(["ignore:old api:DeprecationWarning"]) == []
    assert _unscoped_ignores(["ignore::DeprecationWarning:dbt.adapters"]) == []
    assert _unscoped_ignores([STRICT]) == []


def test_every_exemption_is_scoped(pytestconfig: pytest.Config) -> None:
    offenders = _unscoped_ignores(pytestconfig.getini("filterwarnings"))
    assert not offenders, (
        "scope each exemption to a message or a module, after the error:: line, "
        f"with a comment naming what removes it: {offenders}"
    )
