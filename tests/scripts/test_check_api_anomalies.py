"""Tests for scripts.check_api_anomalies — hourly per-key volume WARNING (#294).

DB-touching aggregation lives in ``src.core.anomaly`` (tested in
``tests/core/test_anomaly.py``); these tests cover the pure reporting layer the
systemd timer invokes.
"""

import asyncio
import logging
import sys
from datetime import UTC, datetime

import pytest

from scripts.check_api_anomalies import main, report, run
from src.core.anomaly import KeyActivity

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _activity(count, *, kid="01TESTKEY", label="Test Key", throttled=0):
    return KeyActivity(
        api_key_id=kid,
        key_label=label,
        request_count=count,
        throttled_count=throttled,
        last_seen=_NOW,
    )


def test_report_warns_above_threshold(caplog):
    with caplog.at_level(logging.INFO):
        anomalous = report([_activity(6000, label="Runaway Key")], threshold=5000)
    assert anomalous == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Runaway Key" in warnings[0].getMessage()
    assert "6000" in warnings[0].getMessage()


def test_report_quiet_below_threshold(caplog):
    with caplog.at_level(logging.INFO):
        anomalous = report([_activity(4999)], threshold=5000)
    assert anomalous == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_report_threshold_boundary_inclusive(caplog):
    """At exactly the threshold the key is anomalous — 'reached' beats 'exceeded'."""
    with caplog.at_level(logging.INFO):
        assert report([_activity(5000)], threshold=5000) == 1


def test_report_unauthenticated_key_labelled(caplog):
    with caplog.at_level(logging.INFO):
        report(
            [
                KeyActivity(
                    api_key_id=None,
                    key_label=None,
                    request_count=9000,
                    throttled_count=0,
                    last_seen=_NOW,
                )
            ],
            threshold=5000,
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "unauthenticated" in warnings[0].getMessage()


def test_report_mentions_throttled_count(caplog):
    with caplog.at_level(logging.INFO):
        report([_activity(7000, throttled=6500)], threshold=5000)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "6500" in warnings[0].getMessage()


def test_report_multiple_keys_one_warning_each(caplog):
    acts = [
        _activity(8000, kid="01A", label="A"),
        _activity(6000, kid="01B", label="B"),
        _activity(10, kid="01C", label="C"),
    ]
    with caplog.at_level(logging.INFO):
        assert report(acts, threshold=5000) == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_default_threshold_is_5000():
    from src.core.anomaly import HOURLY_REQUEST_THRESHOLD

    assert HOURLY_REQUEST_THRESHOLD == 5000


@pytest.mark.parametrize("count,expected", [(0, 0), (1, 0)])
def test_report_empty_or_tiny_activity(caplog, count, expected):
    acts = [_activity(count)] if count else []
    with caplog.at_level(logging.INFO):
        assert report(acts, threshold=5000) == expected


@pytest.mark.parametrize("threshold", [0, -1])
def test_run_nonpositive_threshold_disables_check(monkeypatch, caplog, threshold):
    """Threshold <= 0 disables the check (RATE_LIMIT_* convention) — no DB touched."""
    monkeypatch.delenv("DATABASE_URL", raising=False)  # would RuntimeError if reached
    with caplog.at_level(logging.INFO):
        assert asyncio.run(run(threshold=threshold)) == 0
    assert "disabled" in caplog.text.lower()


def test_main_exits_3_on_anomaly(monkeypatch):
    """Anomaly exit code is 3 — distinct from argparse usage errors (exit 2)."""

    async def fake_run(*, threshold):
        return 1

    monkeypatch.setattr("scripts.check_api_anomalies.run", fake_run)
    monkeypatch.setattr(sys, "argv", ["check_api_anomalies"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 3


def test_main_exits_clean_when_no_anomalies(monkeypatch):
    async def fake_run(*, threshold):
        return 0

    monkeypatch.setattr("scripts.check_api_anomalies.run", fake_run)
    monkeypatch.setattr(sys, "argv", ["check_api_anomalies"])
    main()  # no SystemExit


def test_main_accepts_zero_threshold_as_disabled(monkeypatch):
    """--threshold 0 must not be an argparse usage error — it means disabled."""
    seen = {}

    async def fake_run(*, threshold):
        seen["threshold"] = threshold
        return 0

    monkeypatch.setattr("scripts.check_api_anomalies.run", fake_run)
    monkeypatch.setattr(sys, "argv", ["check_api_anomalies", "--threshold", "0"])
    main()
    assert seen["threshold"] == 0
