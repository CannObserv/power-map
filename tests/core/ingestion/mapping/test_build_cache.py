"""The fixture build partial-parses after the session's first build (#890).

A full `dbt parse` was ~4–6 s of a ~10 s build: every build had a fresh duckdb
path (the profile) and target, so dbt never found its saved manifest. Builds now
share one duckdb path and one target; each keeps its own store, because the
staging models are views over `read_csv`/`read_parquet` of that store's paths,
and a later build must not change what an earlier one's views read.
"""

import pytest

from tests.core.ingestion.mapping.conftest import DEFAULT_CROSSWALK

# Every test here builds the dbt project (#890: the commit hook routes by it).
pytestmark = pytest.mark.dbt

FULL_PARSE = "Unable to do partial parsing"


def test_the_session_full_parses_once(build):
    build()
    build(crosswalk=DEFAULT_CROSSWALK[:-1])
    log = (build.target / "logs" / "dbt.log").read_text()
    assert (build.target / "partial_parse.msgpack").is_file()
    assert log.count(FULL_PARSE) == 1


def test_a_later_build_leaves_an_earlier_ones_views_alone(build):
    first = build()
    later = build(crosswalk=DEFAULT_CROSSWALK[:-2])
    assert first.duckdb_path != later.duckdb_path
    view = "stg_pm__producer_crosswalk"
    assert len(first.rows(view)) == len(DEFAULT_CROSSWALK)
    assert len(later.rows(view)) == len(DEFAULT_CROSSWALK) - 2
