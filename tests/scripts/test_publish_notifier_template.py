"""Tests for scripts.publish_notifier_template — the unit-failure template's publisher (#566).

The wording belongs to this repo (``scripts/notifier/unit-failure.json``);
Notifier stores a copy under power-map's tenant. Dry run by default, like every
writer under ``scripts/`` (#402): it renders the sample through Notifier's
``preview`` and writes nothing without ``--execute``. The key arrives on stdin,
never in argv or the environment.
"""

import io
import json
from types import SimpleNamespace

import pytest

from scripts.notify_unit_failure import TEMPLATE_PATH
from scripts.publish_notifier_template import main, publish

KEY = "nk_test_never_logged_0123456789"
TEMPLATE = json.loads(TEMPLATE_PATH.read_text())


class FakeTemplates:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, **fields):
        self.owner.calls.append(("create", fields))
        return SimpleNamespace(id="01K7NEWTEMPLATE00000000000")

    async def update(self, template_id, **fields):
        self.owner.calls.append(("update", template_id, fields))
        return SimpleNamespace(id=template_id)


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, *, environment="production", preview_error=None, **kwargs):
        self.kwargs = kwargs
        self.environment = environment
        self.preview_error = preview_error
        self.calls: list = []
        self.templates = FakeTemplates(self)
        FakeClient.instances.append(self)

    @classmethod
    def factory(cls, **behaviour):
        return lambda **kwargs: cls(**behaviour, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def health(self):
        return {"environment": self.environment}

    async def preview(self, **kwargs):
        self.calls.append(("preview", kwargs))
        return SimpleNamespace(error=self.preview_error, title="t", body="b")


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.instances = []


async def test_a_dry_run_previews_the_sample_and_writes_nothing():
    out = await publish(
        "http://notifier:9000",
        KEY,
        TEMPLATE,
        template_id="",
        execute=False,
        client_factory=FakeClient.factory(),
    )

    [client] = FakeClient.instances
    assert [c[0] for c in client.calls] == ["preview"]
    assert client.calls[0][1]["variables"] == TEMPLATE["sample_variables"]
    assert out is None


async def test_execute_without_an_id_creates_the_template():
    out = await publish(
        "http://notifier:9000",
        KEY,
        TEMPLATE,
        template_id="",
        execute=True,
        client_factory=FakeClient.factory(),
    )

    kind, fields = FakeClient.instances[0].calls[-1]
    assert kind == "create"
    assert fields["name"] == "power-map-unit-failure"
    assert fields["variables_schema"] == TEMPLATE["variables_schema"]
    assert out == "01K7NEWTEMPLATE00000000000"


async def test_execute_with_an_id_updates_that_template():
    await publish(
        "http://notifier:9000",
        KEY,
        TEMPLATE,
        template_id="01K7OLD",
        execute=True,
        client_factory=FakeClient.factory(),
    )

    kind, template_id, _ = FakeClient.instances[0].calls[-1]
    assert (kind, template_id) == ("update", "01K7OLD")


async def test_a_template_that_does_not_render_writes_nothing():
    with pytest.raises(SystemExit, match="does not render"):
        await publish(
            "http://notifier:9000",
            KEY,
            TEMPLATE,
            template_id="",
            execute=True,
            client_factory=FakeClient.factory(preview_error="undefined variable"),
        )
    assert [c[0] for c in FakeClient.instances[0].calls] == ["preview"]


async def test_a_non_production_notifier_is_refused():
    with pytest.raises(SystemExit, match="production"):
        await publish(
            "http://notifier:9000",
            KEY,
            TEMPLATE,
            template_id="",
            execute=True,
            client_factory=FakeClient.factory(environment="dev"),
        )
    assert FakeClient.instances[0].calls == []


def test_main_reads_the_key_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(KEY + "\n"))

    assert main(["--execute"], client_factory=FakeClient.factory()) == 0
    assert FakeClient.instances[0].kwargs["api_key"] == KEY


def test_main_refuses_an_empty_key(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main([], client_factory=FakeClient.factory()) == 1
    assert FakeClient.instances == []
    assert "stdin" in capsys.readouterr().err
