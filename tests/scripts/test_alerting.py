"""Tests for scripts._alerting — shared GitHub surfacing for scheduled guards.

Extracted from ``scripts/check_ready`` (#347) when #410 needed the same
machinery. The rules it encodes are the ones both guards depend on:

- one open issue per label, never a duplicate;
- silence while that issue is open, because these run on tight cadences;
- a failed ``gh`` call is reported as failed, never as a raised alert;
- summary and journal pointer only — this is a public repo.
"""

import logging

import pytest

from scripts._alerting import Alert, surface

ALERT = Alert(
    label="test-regression",
    title="Test guard failing (automated)",
    subject_recovered="the probe is green again",
    unit="power-map-test",
    label_description="Automated test guard failure",
)


class _Runner:
    """Fake ``gh`` runner recording argv; returns queued (rc, stdout) pairs."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        return self.results.pop(0) if self.results else (0, "")

    def ran(self, *needles):
        return any(all(n in call for n in needles) for call in self.calls)


def _label_present(*rest):
    """Label already exists, then whatever the caller queues."""
    return _Runner((0, ALERT.label), *rest)


def test_opens_an_issue_when_none_is_open():
    runner = _label_present((0, ""))
    surface(False, "something broke", alert=ALERT, runner=runner)
    assert runner.ran("issue", "create")


def test_is_silent_when_an_issue_is_already_open():
    """These guards run every few minutes — a comment per run buries the signal."""
    runner = _label_present((0, "412"))
    surface(False, "something broke", alert=ALERT, runner=runner)
    assert not runner.ran("issue", "create")
    assert not runner.ran("issue", "comment")


def test_closes_the_issue_on_recovery():
    runner = _label_present((0, "412"))
    surface(True, "", alert=ALERT, runner=runner)
    assert runner.ran("issue", "comment", "412")
    assert runner.ran("issue", "close", "412")


def test_recovery_is_a_no_op_when_nothing_is_open():
    runner = _label_present((0, ""))
    surface(True, "", alert=ALERT, runner=runner)
    assert not runner.ran("issue", "close")


def test_skips_when_the_issue_list_call_fails():
    """A transient gh failure must not open a duplicate (a11y #369 CR2 finding 5)."""
    runner = _label_present((1, ""))
    surface(False, "something broke", alert=ALERT, runner=runner)
    assert not runner.ran("issue", "create")


def test_creates_the_label_idempotently():
    runner = _Runner((0, ""), (0, ""))  # label list empty -> create it
    surface(False, "something broke", alert=ALERT, runner=runner)
    assert runner.ran("label", "create", ALERT.label)


def test_body_carries_the_summary_and_journal_pointer_only():
    runner = _label_present((0, ""))
    surface(False, "reason `pool_timeout`", alert=ALERT, runner=runner)
    create = next(c for c in runner.calls if "create" in c)
    body = create[create.index("--body") + 1]
    assert "journalctl -u power-map-test" in body
    assert "reason `pool_timeout`" in body


def test_does_not_claim_to_have_opened_when_create_fails(caplog):
    """A log line reporting an unchecked outcome is worse than silence."""
    runner = _label_present((0, ""), (1, ""))
    with caplog.at_level(logging.INFO):
        surface(False, "something broke", alert=ALERT, runner=runner)
    messages = [r.getMessage().lower() for r in caplog.records]
    assert any("failed" in m for m in messages)
    assert not any("opened" in m for m in messages)


def test_does_not_claim_to_have_closed_when_close_fails(caplog):
    runner = _label_present((0, "412"), (0, ""), (1, ""))
    with caplog.at_level(logging.INFO):
        surface(True, "", alert=ALERT, runner=runner)
    messages = [r.getMessage().lower() for r in caplog.records]
    assert any("failed" in m for m in messages)
    assert not any("closed recovered" in m for m in messages)


def test_reports_a_successful_open(caplog):
    runner = _label_present((0, ""), (0, ""))
    with caplog.at_level(logging.INFO):
        surface(False, "something broke", alert=ALERT, runner=runner)
    assert any("opened" in r.getMessage().lower() for r in caplog.records)


def test_reports_a_successful_close(caplog):
    runner = _label_present((0, "412"), (0, ""), (0, ""))
    with caplog.at_level(logging.INFO):
        surface(True, "", alert=ALERT, runner=runner)
    assert any("closed recovered" in r.getMessage().lower() for r in caplog.records)


def test_gh_failure_never_propagates(caplog):
    """Surfacing is best-effort: it must not change a guard's exit code."""

    def boom(args):
        raise OSError("gh exploded")

    with caplog.at_level(logging.WARNING):
        surface(False, "something broke", alert=ALERT, runner=boom)  # must not raise


@pytest.mark.parametrize("existing", ["", "null"])
def test_empty_and_null_both_mean_no_open_issue(existing):
    """`gh -q '.[0].number'` prints `null` for an empty list, not an empty string."""
    runner = _label_present((0, existing))
    surface(False, "something broke", alert=ALERT, runner=runner)
    assert runner.ran("issue", "create")


# --- CR1 finding 5: the comment's return code is checked too -----------------


def test_reports_a_failed_recovery_comment(caplog):
    """Same class as the create-path defect #414 fixed — do not skip the rc."""
    runner = _label_present((0, "412"), (1, ""), (0, ""))  # comment FAILS, close ok
    with caplog.at_level(logging.INFO):
        surface(True, "", alert=ALERT, runner=runner)
    messages = [r.getMessage().lower() for r in caplog.records]
    assert any("comment failed" in m for m in messages)


def test_a_failed_comment_still_closes_the_issue(caplog):
    """The close is what matters; a missing comment must not strand the alert."""
    runner = _label_present((0, "412"), (1, ""), (0, ""))
    with caplog.at_level(logging.INFO):
        surface(True, "", alert=ALERT, runner=runner)
    assert runner.ran("issue", "close", "412")
    assert any("closed recovered" in r.getMessage().lower() for r in caplog.records)
