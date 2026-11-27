"""Tests for scripts/org_kind_gold.py — the org-kind gold set (#877 phase 2b).

``sample`` draws the stratified sample into a private worksheet, read-only,
and refuses to write inside any git work tree: the repo is public, and only
``(org_id, kind)`` is ever committed. The DB-backed tests need
TEST_DATABASE_URL and run in a rolled-back transaction.
"""

import csv
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from scripts import org_kind_gold as g
from src.core.db import generate_id

REPO = Path(__file__).resolve().parents[2]


# --- the private-path guard: pure ------------------------------------------


def test_refuses_a_path_inside_a_work_tree():
    with pytest.raises(g.PrivatePathError, match="not ignored"):
        g.require_private(REPO / "docs" / "worksheet.csv")


def test_accepts_a_path_outside_every_work_tree(tmp_path):
    g.require_private(tmp_path / "worksheet.csv")


def test_refuses_a_symlink_into_a_work_tree(tmp_path):
    link = tmp_path / "into-repo"
    link.symlink_to(REPO / "docs")
    with pytest.raises(g.PrivatePathError):
        g.require_private(link / "worksheet.csv")


def _repo(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / ".gitignore").write_text("private/\n")
    return repo


def test_accepts_an_ignored_path_inside_a_work_tree(tmp_path):
    repo = _repo(tmp_path)
    g.require_private(repo / "private" / "worksheet.csv")


def test_refuses_an_unignored_path_inside_a_work_tree(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(g.PrivatePathError, match="not ignored"):
        g.require_private(repo / "public" / "worksheet.csv")


def test_the_repo_ignores_the_gold_set_folder():
    g.require_private(REPO / "data" / "gold-877" / "worksheet.csv")


def test_quotas_total_about_three_hundred():
    assert 280 <= sum(g.QUOTAS.values()) <= 320
    assert list(g.QUOTAS)[-1] == "no_identifier"


def test_ambiguous_titles_are_the_303_d2_list():
    assert {"member", "chair", "director", "treasurer", "principal officer"} <= set(
        g.AMBIGUOUS_TITLES
    )
    assert all(t == t.lower() for t in g.AMBIGUOUS_TITLES)


# --- DB-backed --------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, name, *slugs, titles=(), old=True):
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, created_at) VALUES"
        " ($1, CASE WHEN $2 THEN NOW() - interval '1 year' ELSE NOW() END)",
        oid,
        old,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        oid,
        name,
    )
    for slug in slugs:
        type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            oid,
            type_id,
            f"v-{generate_id()[-8:]}",
        )
    for title in titles:
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
            generate_id(),
            oid,
            title,
        )
    return oid


@pytest.mark.integration
async def test_each_org_lands_in_its_first_matching_stratum(db):
    recent_party = await _org(db, "Recent Party", "org_wa_party", old=False)
    party = await _org(db, "Old Party", "org_wa_party")
    wslcb = await _org(db, "Licensee", "org_wslcb", "org_ubi")
    amb = await _org(db, "Some Guild", titles=("Member",))
    plain = await _org(db, "Plain Org", titles=("Owner",))

    rows = {r.org_id: r for r in await g.sample(db)}

    assert rows[recent_party].stratum == "recent"
    assert rows[party].stratum == "party"
    assert rows[wslcb].stratum == "wslcb"
    assert rows[amb].stratum == "ambiguous_title"
    assert rows[plain].stratum == "no_identifier"
    assert rows[amb].name == "Some Guild"
    assert rows[amb].role_titles == "Member"
    assert rows[wslcb].listed_in  # the registries' full names, never the values


@pytest.mark.integration
async def test_quotas_cap_each_stratum_deterministically(db, monkeypatch):
    monkeypatch.setitem(g.QUOTAS, "no_identifier", 3)
    for i in range(6):
        await _org(db, f"Filler {i}")
    first = [r.org_id for r in await g.sample(db) if r.stratum == "no_identifier"]
    again = [r.org_id for r in await g.sample(db) if r.stratum == "no_identifier"]
    assert len(first) == 3
    assert first == again


@pytest.mark.integration
async def test_archived_orgs_are_not_sampled(db):
    oid = await _org(db, "Archived Org")
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", oid)
    assert oid not in {r.org_id for r in await g.sample(db)}


@pytest.mark.integration
async def test_write_worksheet_has_blank_label_columns(db, tmp_path):
    await _org(db, "Sheet Org", titles=("Owner",))
    rows = await g.sample(db)
    out = tmp_path / "worksheet.csv"
    g.write_worksheet(rows, out)
    with out.open(newline="") as f:
        read = list(csv.DictReader(f))
    assert list(read[0]) == list(g.WORKSHEET_COLUMNS)
    assert all(r["kind"] == "" and r["unsure"] == "" for r in read)
    assert out.stat().st_mode & 0o077 == 0


# --- the review pick: pure --------------------------------------------------


def _sheet(tmp_path, n_sure=50, n_unsure=4):
    path = tmp_path / "worksheet.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(g.WORKSHEET_COLUMNS)
        for i in range(n_sure):
            w.writerow([f"S{i:03d}", "no_identifier", f"Org {i}", "", "", "business", "", ""])
        for i in range(n_unsure):
            w.writerow([f"U{i:03d}", "pdc", f"Odd {i}", "", "", "coalition", "Y", "why"])
    return path


def test_review_takes_every_unsure_row_and_a_seeded_random_thirty(tmp_path):
    rows = g.read_worksheet(_sheet(tmp_path))
    picked = g.review_pick(rows)
    unsure = [r for r in picked if r["unsure"] == "Y"]
    sure = [r for r in picked if r["unsure"] != "Y"]
    assert len(unsure) == 4
    assert len(sure) == g.REVIEW_RANDOM == 30
    assert [r["org_id"] for r in g.review_pick(rows)] == [r["org_id"] for r in picked]


def test_review_refuses_an_unlabelled_worksheet(tmp_path):
    path = _sheet(tmp_path)
    rows = g.read_worksheet(path)
    rows[0]["kind"] = ""
    with pytest.raises(ValueError, match="unlabelled"):
        g.review_pick(rows)


def test_review_refuses_a_kind_outside_the_vocabulary(tmp_path):
    rows = g.read_worksheet(_sheet(tmp_path))
    rows[0]["kind"] = "government_agency"
    with pytest.raises(ValueError, match="government_agency"):
        g.review_pick(rows)


def test_kinds_are_the_seeded_org_kinds_plus_none():
    schema = (REPO / "src" / "core" / "schema.sql").read_text()
    block = schema[schema.index("INSERT INTO _seed_org_kinds") :]
    block = block[: block.index(";")]
    seeded = {k for k in g.KINDS - {"none"} if f"'{k}'" in block}
    assert seeded == g.KINDS - {"none"}
    assert block.count("'01KZ") == len(g.KINDS) - 1


def _seeded_descriptions():
    schema = (REPO / "src" / "core" / "schema.sql").read_text()
    block = schema[schema.index("INSERT INTO _seed_org_kinds") :]
    block = block[: block.index(";")].replace("''", "'")
    out = {}
    for kind in g.KINDS - {"none"}:
        tail = block[block.index(f"'{kind}'") :]
        out[kind] = tail.split("\n", 2)[1].strip().strip(",)")
    return out


def test_the_definitions_carry_the_operators_class_rulings():
    """2026-11-27 rulings, so Jev is asked what the gold set scores (#877)."""
    d = _seeded_descriptions()
    assert "governments" in d["association"]  # ruling 1: members are governments
    assert "task force" in d["legislature"]  # ruling 2: a legislature's task force
    assert "legislative task force" in d["government"]  # ...named as the exception
    assert "federation of unions" in d["labor_union"]  # ruling 3
    assert "city or county council" in d["government"]  # Longview stays government
