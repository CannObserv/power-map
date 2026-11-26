"""Tests for src/core/jev_client.py — TypeSafe Jev transport (#877).

Every test runs against ``httpx.MockTransport``; nothing here calls the API.
"""

import json
import logging

import httpx
import pytest

from src.core import jev_client as jc
from src.core import org_kind_question as q

KEY = "sk-test-SECRET-do-not-log"
CRIT = q.criteria([("business", "A company"), ("nonprofit", "A charity")])
ORG = q.OrgState(name="Acme Co", listed_in=(), role_titles=("Owner",))


def _reply(choice="business", confidence=0.91, *, tokens=812, model="jev-1.14.0", **extra):
    probs = {k: 0.0 for k in CRIT}
    probs[choice] = confidence
    answer = {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probs}
    answer.update(extra)
    return {
        "model": model,
        "answers": {"kind": answer},
        "usage": {"input_tokens": tokens, "output_tokens": 40},
    }


def _client(*responses):
    """A client whose transport returns *responses* in order and records requests."""
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def _no_sleep(_s: float) -> None:
    return None


async def _ask(client, **kw):
    return await jc.ask_kind(
        ORG, CRIT, api_key=KEY, model="jev-1.14.0", client=client, sleep=_no_sleep, **kw
    )


# --- config --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "on"),
    [("1", True), ("true", True), ("YES", True), ("", False), ("0", False), ("off", False)],
)
def test_enabled_flag(monkeypatch, raw, on):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", raw)
    assert jc.is_enabled() is on


def test_enabled_flag_unset_is_off(monkeypatch):
    monkeypatch.delenv("JEV_SUGGESTIONS_ENABLED", raising=False)
    assert jc.is_enabled() is False


def test_model_defaults_to_the_pin(monkeypatch):
    monkeypatch.delenv("JEV_MODEL", raising=False)
    assert jc.get_model() == "jev-1.14.0"
    monkeypatch.setenv("JEV_MODEL", " jev-2.0.0 ")
    assert jc.get_model() == "jev-2.0.0"


@pytest.mark.parametrize(("raw", "want"), [("", 0.7), ("0.85", 0.85), ("1.5", 0.7), ("x", 0.7)])
def test_threshold(monkeypatch, raw, want):
    monkeypatch.setenv("JEV_CONFIDENCE_THRESHOLD", raw)
    assert jc.confidence_threshold() == want


@pytest.mark.parametrize(("raw", "want"), [("", 2000), ("50", 50), ("0", 2000), ("x", 2000)])
def test_max_per_run(monkeypatch, raw, want):
    monkeypatch.setenv("JEV_MAX_PER_RUN", raw)
    assert jc.max_per_run() == want


# --- requests ------------------------------------------------------------


async def test_ask_sends_one_org_and_parses_the_answer():
    client, seen = _client((200, _reply()))
    answer = await _ask(client)
    assert answer == jc.JevAnswer(
        kind="business",
        confidence=0.91,
        probabilities={"business": 0.91, "nonprofit": 0.0, "none": 0.0},
        model="jev-1.14.0",
        input_tokens=812,
    )
    (req,) = seen
    assert str(req.url) == jc.URL
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body == {
        "model": "jev-1.14.0",
        "state": q.build_state(ORG),
        "questions": q.build_questions(CRIT),
    }


async def test_missing_key_fails_before_any_request():
    client, seen = _client()
    with pytest.raises(jc.JevError, match="TYPESAFE_API_KEY"):
        await jc.ask_kind(ORG, CRIT, api_key="", model="jev-1.14.0", client=client)
    assert seen == []


async def test_retries_429_and_529_with_backoff():
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    client, seen = _client((429, {}), (529, {}), (200, _reply()))
    answer = await jc.ask_kind(
        ORG, CRIT, api_key=KEY, model="jev-1.14.0", client=client, sleep=sleep
    )
    assert answer.kind == "business"
    assert len(seen) == 3
    assert waits == [1, 2]


async def test_gives_up_after_max_attempts():
    client, seen = _client(*[(429, {})] * 3)
    with pytest.raises(jc.JevError) as exc:
        await _ask(client, max_attempts=3)
    assert exc.value.status == 429
    assert len(seen) == 3


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retried_and_not_quoted(status):
    client, seen = _client((status, f"bad key {KEY[:12]}"))
    with pytest.raises(jc.JevError) as exc:
        await _ask(client)
    assert exc.value.auth
    assert len(seen) == 1
    assert KEY[:12] not in str(exc.value)


async def test_other_status_quotes_a_short_excerpt():
    client, _ = _client((400, "schema says no " + "x" * 500))
    with pytest.raises(jc.JevError) as exc:
        await _ask(client)
    assert "schema says no" in str(exc.value)
    assert len(str(exc.value)) < 300


async def test_transport_error_names_only_its_type():
    client, _ = _client(httpx.ConnectError(f"boom {KEY}"))
    with pytest.raises(jc.JevError) as exc:
        await _ask(client)
    assert str(exc.value) == "Jev request failed: ConnectError"


@pytest.mark.parametrize(
    "reply",
    [
        _reply(choice="tribe"),  # not an option
        _reply(confidence=1.2),
        _reply(confidence=True),
        _reply(tokens=-1),
        {"model": "jev-1.14.0", "usage": {"input_tokens": 1}},
        _reply(probabilities={"business": "high"}),
        "not json",
    ],
)
async def test_malformed_reply_fails(reply):
    client, _ = _client((200, reply))
    with pytest.raises(jc.JevError, match="malformed"):
        await _ask(client)


async def test_missing_probabilities_store_none():
    reply = _reply()
    del reply["answers"]["kind"]["probabilities"]
    client, _ = _client((200, reply))
    assert (await _ask(client)).probabilities is None


async def test_echoed_model_wins_and_a_blank_echo_falls_back():
    client, _ = _client((200, _reply(model="jev-1.14.1")), (200, _reply(model="")))
    assert (await _ask(client)).model == "jev-1.14.1"
    assert (await _ask(client)).model == "jev-1.14.0"


async def test_key_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = _client((429, {}), (401, KEY), httpx.ConnectError(KEY))
    with pytest.raises(jc.JevError):
        await _ask(client)
    with pytest.raises(jc.JevError):
        await _ask(client)
    assert KEY not in caplog.text
