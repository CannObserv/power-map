"""Tests for GET /admin/_dup-badge/{type}/ async badge endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.api.main import app

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# People — card variant
# ---------------------------------------------------------------------------


def test_people_card_returns_link_when_nonzero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 4
    try:
        resp = client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "4 duplicate" in resp.text
    assert "/admin/people/duplicates/" in resp.text


def test_people_card_empty_when_zero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 0
    try:
        resp = client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


def test_people_card_singular_label(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 1
    try:
        resp = client.get("/admin/_dup-badge/people/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "1 duplicate →" in resp.text
    assert "1 duplicates" not in resp.text


# ---------------------------------------------------------------------------
# People — banner variant
# ---------------------------------------------------------------------------


def test_people_banner_returns_alert_when_nonzero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 3
    try:
        resp = client.get("/admin/_dup-badge/people/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "possible duplicate" in resp.text.lower()
    assert "/admin/people/duplicates/" in resp.text


def test_people_banner_empty_when_zero(client):
    app.dependency_overrides[get_person_dup_count] = lambda: 0
    try:
        resp = client.get("/admin/_dup-badge/people/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Orgs — card variant
# ---------------------------------------------------------------------------


def test_orgs_card_returns_link_when_nonzero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 7
    try:
        resp = client.get("/admin/_dup-badge/orgs/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert "7 duplicate" in resp.text
    assert "/admin/orgs/duplicates/" in resp.text


def test_orgs_card_empty_when_zero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 0
    try:
        resp = client.get("/admin/_dup-badge/orgs/?variant=card", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Orgs — banner variant
# ---------------------------------------------------------------------------


def test_orgs_banner_returns_alert_when_nonzero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 2
    try:
        resp = client.get("/admin/_dup-badge/orgs/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert "possible duplicate" in resp.text.lower()
    assert "/admin/orgs/duplicates/" in resp.text


def test_orgs_banner_empty_when_zero(client):
    app.dependency_overrides[get_org_dup_count] = lambda: 0
    try:
        resp = client.get("/admin/_dup-badge/orgs/?variant=banner", headers=HTMX_HEADERS)
    finally:
        app.dependency_overrides.pop(get_org_dup_count, None)
    assert resp.status_code == 200
    assert resp.text.strip() == ""


# ---------------------------------------------------------------------------
# Guard: non-HTMX and unknown type
# ---------------------------------------------------------------------------


def test_rejects_non_htmx_request(client):
    resp = client.get("/admin/_dup-badge/people/?variant=card", headers=AUTH_HEADERS)
    assert resp.status_code == 400


def test_unknown_type_returns_404(client):
    resp = client.get("/admin/_dup-badge/invalid/?variant=card", headers=HTMX_HEADERS)
    assert resp.status_code == 404
