"""HTTP client for TypeSafe Jev org-kind suggestions (#877).

Pure transport: no database. Owns the request shape, the configuration reads,
the 429/529 retry policy and the response validation. The question itself
(state, criteria, instructions) is built in ``src/core/org_kind_question.py``,
which the gold set scores; the orchestration (which orgs, what is stored) is
``src/core/classification.py``'s drain.

Modelled on the WSLCB Licensing Tracker's ``jev_client.py``: plain ``httpx``,
the API key read at call time and never logged nor quoted in an error. One org
per request, so production asks exactly the question the gold set scored. A
failed or malformed reply raises :class:`JevError` and stores nothing; the org
stays in the backlog for the next drain.

Configuration (environment, read at call time):
    JEV_SUGGESTIONS_ENABLED   1/true/yes to call Jev; anything else, off
    TYPESAFE_API_KEY          the API key
    JEV_MODEL                 pinned model, default ``jev-1.14.0``
    JEV_CONFIDENCE_THRESHOLD  0–1, default 0.7: below it, an answer goes to review
    JEV_MAX_PER_RUN           orgs asked per drain, default 2000
"""

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.core import org_kind_question as q
from src.core.logging import get_logger

logger = get_logger(__name__)

URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.14.0"
DEFAULT_THRESHOLD = 0.7
DEFAULT_MAX_PER_RUN = 2000
TIMEOUT = 60.0
MAX_ATTEMPTS = 6
# Rate limit and overload; slept 2**attempt seconds (1, 2, 4, 8, 16).
RETRY_STATUSES = frozenset({429, 529})
AUTH_STATUSES = frozenset({401, 403})
ERROR_EXCERPT_CHARS = 200

ENABLED_ENV = "JEV_SUGGESTIONS_ENABLED"
API_KEY_ENV = "TYPESAFE_API_KEY"
MODEL_ENV = "JEV_MODEL"
THRESHOLD_ENV = "JEV_CONFIDENCE_THRESHOLD"
MAX_PER_RUN_ENV = "JEV_MAX_PER_RUN"


@dataclass(frozen=True)
class JevAnswer:
    """One org's answer: the chosen kind (or ``none``) and what it cost."""

    kind: str
    confidence: float
    probabilities: dict[str, float] | None
    model: str
    input_tokens: int


class JevError(Exception):
    """A request that stored nothing. ``status`` is the HTTP status, or None."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Record the message and the HTTP status (None when no response came)."""
        super().__init__(message)
        self.status = status

    @property
    def auth(self) -> bool:
        """True for 401/403: a wrong or revoked key, which no retry fixes."""
        return self.status in AUTH_STATUSES


def is_enabled() -> bool:
    """Return True if ``JEV_SUGGESTIONS_ENABLED`` is 1/true/yes. Gates calls only."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def get_api_key() -> str:
    """Return ``TYPESAFE_API_KEY`` ("" when unset)."""
    return os.environ.get(API_KEY_ENV, "").strip()


def get_model() -> str:
    """Return the pinned model: ``JEV_MODEL`` or ``DEFAULT_MODEL``."""
    return os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def confidence_threshold() -> float:
    """Return ``JEV_CONFIDENCE_THRESHOLD`` in [0, 1]; anything else warns and gives 0.7."""
    raw = os.environ.get(THRESHOLD_ENV, "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not 0.0 <= value <= 1.0:
        logger.warning("%s=%r is not in [0, 1]; using %s", THRESHOLD_ENV, raw, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    return value


def max_per_run() -> int:
    """Return ``JEV_MAX_PER_RUN``; a non-integer or a value below 1 warns and gives 2000."""
    raw = os.environ.get(MAX_PER_RUN_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_PER_RUN
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        logger.warning(
            "%s=%r is not a positive integer; using %d", MAX_PER_RUN_ENV, raw, DEFAULT_MAX_PER_RUN
        )
        return DEFAULT_MAX_PER_RUN
    return value


def build_request(org: q.OrgState, crit: dict[str, str], model: str) -> dict:
    """Return the request body for one org."""
    return {"model": model, "state": q.build_state(org), "questions": q.build_questions(crit)}


def _malformed(detail: str) -> JevError:
    return JevError(f"malformed Jev response: {detail}", status=httpx.codes.OK)


def _is_unit(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def parse_response(data: object, crit: dict[str, str], model: str) -> JevAnswer:
    """Validate a reply body; raise :class:`JevError` if anything is off.

    A missing or blank ``model`` echo falls back to the requested *model*.
    """
    if not isinstance(data, dict):
        raise _malformed("body is not an object")
    usage = data.get("usage")
    tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise _malformed("usage.input_tokens is not a non-negative integer")
    answers = data.get("answers")
    answer = answers.get(q.QUESTION_NAME) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise _malformed(f"no answer object for {q.QUESTION_NAME}")
    choice = answer.get("choice")
    if choice not in crit:
        raise _malformed(f"choice {choice!r} is not an option")
    confidence = answer.get("confidence")
    if not _is_unit(confidence):
        raise _malformed(f"confidence {confidence!r} is not in [0, 1]")
    probs = answer.get("probabilities")
    if probs is not None and (
        not isinstance(probs, dict) or not all(k in crit and _is_unit(v) for k, v in probs.items())
    ):
        raise _malformed("probabilities are not option → [0, 1]")
    echoed = data.get("model")
    return JevAnswer(
        kind=choice,
        confidence=float(confidence),
        probabilities={k: float(v) for k, v in probs.items()} if probs is not None else None,
        model=echoed if isinstance(echoed, str) and echoed.strip() else model,
        input_tokens=tokens,
    )


def _excerpt(response: httpx.Response) -> str:
    """``": <body>"``, whitespace collapsed and cut to ``ERROR_EXCERPT_CHARS``; "" if none."""
    try:
        text = " ".join(response.text.split())
    except Exception:  # noqa: BLE001 — an error without its detail beats none
        return ""
    if len(text) > ERROR_EXCERPT_CHARS:
        text = text[:ERROR_EXCERPT_CHARS] + "…"
    return f": {text}" if text else ""


async def ask_kind(
    org: q.OrgState,
    crit: dict[str, str],
    *,
    api_key: str,
    model: str,
    client: httpx.AsyncClient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_attempts: int = MAX_ATTEMPTS,
    timeout: float = TIMEOUT,
) -> JevAnswer:
    """Ask Jev for one org's kind.

    Retries 429/529 up to *max_attempts* attempts, sleeping ``2**attempt``
    seconds between them. Raises :class:`JevError` for a missing key (before
    any request), any other non-200 status, exhausted retries, a transport
    error, or a malformed reply.
    """
    if not api_key:
        raise JevError(f"{API_KEY_ENV} is not set")
    body = build_request(org, crit, model)
    headers = {"Authorization": f"Bearer {api_key}"}
    for attempt in range(max_attempts):
        try:
            response = await client.post(URL, json=body, headers=headers, timeout=timeout)
        except httpx.RequestError as e:
            # By type only: the message may echo request details.
            raise JevError(f"Jev request failed: {type(e).__name__}") from None
        status = response.status_code
        if status in RETRY_STATUSES:
            if attempt + 1 < max_attempts:
                wait = 2**attempt
                logger.warning(
                    "Jev: HTTP %d on attempt %d/%d, retrying in %ds",
                    status,
                    attempt + 1,
                    max_attempts,
                    wait,
                )
                await sleep(wait)
                continue
            raise JevError(f"Jev: HTTP {status} after {max_attempts} attempts", status=status)
        if status != httpx.codes.OK:
            # Never quote an auth rejection: some vendors echo a key prefix in it.
            detail = "" if status in AUTH_STATUSES else _excerpt(response)
            raise JevError(f"Jev: HTTP {status}{detail}", status=status)
        try:
            data = response.json()
        except ValueError:
            raise _malformed("body is not JSON") from None
        return parse_response(data, crit, model)
    raise AssertionError  # pragma: no cover — the loop always returns or raises
