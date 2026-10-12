"""Tests for scripts.notify_unit_failure — power-map.service's OnFailure= dispatch (#566).

``power-map-notify-failure@.service`` runs this after its ``logger`` line, the
fallback, has already gone to the journal. It dispatches one Notifier message per
failed invocation (CannObserv/notifier#114) and never fails itself: a failing
handler would need a handler.

The contract pinned on notifier#114: a stored template whose JSON lives in this
repo; variables ``unit``, ``result``, ``host`` and ``journal_cmd`` and **no
journal text** (public repo); ``idempotency_key = {unit}:{invocation_id}``; a
check that the endpoint is production before sending.
"""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from notifier_client import AuthError

from scripts.notify_unit_failure import (
    CREDENTIAL,
    TEMPLATE_PATH,
    failure_from_env,
    load_config,
    main,
    notify,
)

UNIT = "power-map.service"
KEY = "nk_test_never_logged_0123456789"
TEMPLATE_ID = "01K7TEMPLATE00000000000000"
CHANNELS = "01K7CHANNELA0000000000000A,01K7CHANNELB0000000000000B"


@pytest.fixture
def creds(tmp_path):
    (tmp_path / CREDENTIAL).write_text(KEY + "\n")
    return tmp_path


def _env(creds: Path, **overrides: str) -> dict[str, str]:
    env = {
        "NOTIFIER_URL": "http://notifier:9000",
        "NOTIFIER_TEMPLATE_ID": TEMPLATE_ID,
        "NOTIFIER_CHANNEL_IDS": CHANNELS,
        "CREDENTIALS_DIRECTORY": str(creds),
        "MONITOR_SERVICE_RESULT": "start-limit-hit",
        "MONITOR_EXIT_CODE": "exited",
        "MONITOR_EXIT_STATUS": "1",
        "MONITOR_INVOCATION_ID": "abc123",
    }
    env.update(overrides)
    return env


class FakeClient:
    """Stands in for ``NotifierClient``: records the dispatch, answers health."""

    instances: list["FakeClient"] = []

    def __init__(self, *, environment="production", status="succeeded", error=None, **kwargs):
        self.kwargs = kwargs
        self.environment = environment
        self.status = status
        self.error = error
        self.dispatched: list[dict] = []
        FakeClient.instances.append(self)

    @classmethod
    def factory(cls, **behaviour):
        def make(**kwargs):
            return cls(**behaviour, **kwargs)

        return make

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def health(self):
        return {"status": "ok", "environment": self.environment}

    async def dispatch(self, **kwargs):
        if self.error:
            raise self.error
        self.dispatched.append(kwargs)
        attempt = SimpleNamespace(status=self.status)
        return SimpleNamespace(
            id="01K7DISPATCH0000000000000", status=self.status, attempts=[attempt]
        )


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.instances = []


# --- config -----------------------------------------------------------------


def test_load_config_reads_the_key_from_the_credential(creds):
    config = load_config(_env(creds))

    assert config.url == "http://notifier:9000"
    assert config.api_key == KEY
    assert config.template_id == TEMPLATE_ID
    assert config.channel_ids == CHANNELS.split(",")


@pytest.mark.parametrize(
    "missing", ["NOTIFIER_TEMPLATE_ID", "NOTIFIER_CHANNEL_IDS", "NOTIFIER_URL"]
)
def test_an_unprovisioned_piece_means_journal_only(creds, missing, caplog):
    assert load_config(_env(creds, **{missing: ""})) is None
    assert missing in caplog.text


def test_an_empty_credential_means_journal_only(tmp_path, caplog):
    """``SetCredential=notifier-api:`` is the fallback when the key file is absent."""
    (tmp_path / CREDENTIAL).write_text("")

    assert load_config(_env(tmp_path)) is None
    assert "key" in caplog.text


# --- the failure ------------------------------------------------------------


def test_the_idempotency_key_is_unit_and_invocation(creds):
    failure = failure_from_env(UNIT, _env(creds))

    assert failure.idempotency_key == f"{UNIT}:abc123"
    assert failure.result == "start-limit-hit (exited 1)"


def test_a_manual_trigger_gets_a_fresh_key(creds):
    env = _env(creds)
    for name in ("MONITOR_SERVICE_RESULT", "MONITOR_EXIT_CODE", "MONITOR_EXIT_STATUS"):
        del env[name]
    del env["MONITOR_INVOCATION_ID"]

    first = failure_from_env(UNIT, env)
    second = failure_from_env(UNIT, env)

    assert first.result == "manual"
    assert first.idempotency_key.startswith(f"{UNIT}:manual:")
    assert first.idempotency_key != second.idempotency_key


# --- the dispatch -----------------------------------------------------------


async def test_notify_dispatches_once_with_the_pinned_contract(creds):
    config = load_config(_env(creds))
    failure = failure_from_env(UNIT, _env(creds))

    status = await notify(config, failure, host="power-map", client_factory=FakeClient.factory())

    assert status == "succeeded"
    [client] = FakeClient.instances
    assert client.kwargs["api_key"] == KEY
    [sent] = client.dispatched
    assert sent["template_id"] == TEMPLATE_ID
    assert sent["channel_ids"] == CHANNELS.split(",")
    assert sent["idempotency_key"] == f"{UNIT}:abc123"
    assert sent["variables"] == {
        "unit": UNIT,
        "result": "start-limit-hit (exited 1)",
        "host": "power-map",
        "journal_cmd": f"journalctl -u {UNIT} -n 100",
    }
    assert sent["metadata"]["invocation_id"] == "abc123"


async def test_a_non_production_notifier_gets_nothing(creds, caplog):
    config = load_config(_env(creds))
    failure = failure_from_env(UNIT, _env(creds))

    status = await notify(
        config, failure, host="power-map", client_factory=FakeClient.factory(environment="dev")
    )

    assert status is None
    assert FakeClient.instances[0].dispatched == []
    assert "dev" in caplog.text


async def test_a_partial_delivery_is_a_warning(creds, caplog):
    config = load_config(_env(creds))
    failure = failure_from_env(UNIT, _env(creds))

    await notify(
        config, failure, host="power-map", client_factory=FakeClient.factory(status="partial")
    )

    assert any(r.levelno == logging.WARNING and "partial" in r.getMessage() for r in caplog.records)


# --- main: fail-open --------------------------------------------------------


def test_main_dispatches_and_returns_zero(creds):
    assert main([UNIT], _env(creds), client_factory=FakeClient.factory()) == 0
    assert len(FakeClient.instances[0].dispatched) == 1


@pytest.mark.parametrize(
    "error",
    [
        AuthError("bad key", status_code=401),
        httpx.ConnectError("refused"),
        TimeoutError(),
        RuntimeError("bug"),
    ],
)
def test_main_returns_zero_whatever_goes_wrong(creds, error, caplog):
    assert main([UNIT], _env(creds), client_factory=FakeClient.factory(error=error)) == 0
    assert "journal line stands" in caplog.text
    assert KEY not in caplog.text


def test_main_without_config_returns_zero_and_sends_nothing(creds):
    assert main([UNIT], _env(creds, NOTIFIER_URL=""), client_factory=FakeClient.factory()) == 0
    assert FakeClient.instances == []


def test_bad_usage_returns_zero(caplog):
    assert main([], {}, client_factory=FakeClient.factory()) == 0
    assert "usage" in caplog.text


# --- the template the dispatch names ----------------------------------------


def test_the_template_requires_the_four_variables_and_no_journal_text():
    template = json.loads(TEMPLATE_PATH.read_text())
    schema = template["variables_schema"]

    assert set(schema["required"]) == {"unit", "result", "host", "journal_cmd"}
    assert schema["additionalProperties"] is True
    assert "journal_tail" not in json.dumps(template)
    assert set(template["sample_variables"]) == {"unit", "result", "host", "journal_cmd"}
    for field in ("title_template", "body_template"):
        assert "{{ unit }}" in template[field]
    assert "{{ journal_cmd }}" in template["body_template"]
