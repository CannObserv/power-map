"""Where the subscription comes from (#536).

Each subscribed dataset is pinned in `meta` on its source in the mapping
project's `sources.yml`, beside the `read_csv` that depends on its shape. The
puller reads that file as YAML: `src.core.ingestion.mapping` imports dbt, which
the nightly unit's environment does not carry.
"""

import pytest

from src.core.ingestion.datasets import Pin, load_subscription

PERSONS = "aa" * 32
ROLES = "bb" * 32

SOURCES = f"""
version: 2
sources:
  - name: usa_wa
    tables:
      - name: persons
        meta:
          external_location: "read_csv('x')"
          schema_major: 2
          contract_hash: "sha256:{PERSONS}"
      - name: roles
        meta:
          schema_major: 1
          contract_hash: "sha256:{ROLES}"
  - name: pm
    tables:
      - name: curation_overlay
        meta:
          external_location: "read_parquet('y')"
"""


def _write(tmp_path, text):
    path = tmp_path / "sources.yml"
    path.write_text(text)
    return path


def test_every_table_of_the_named_source_is_pinned_and_no_other(tmp_path):
    """PM's own Parquet exports are sources too; they are not the publisher's."""
    subscription = load_subscription(_write(tmp_path, SOURCES), source="usa_wa")

    assert dict(subscription.pins) == {"persons": Pin(2, PERSONS), "roles": Pin(1, ROLES)}


def test_a_source_table_without_a_pin_is_refused(tmp_path):
    """Skipping it would leave its model reading whatever the store last held."""
    text = SOURCES.replace("          schema_major: 1\n", "")

    with pytest.raises(ValueError, match=r"usa_wa\.roles.*schema_major"):
        load_subscription(_write(tmp_path, text), source="usa_wa")


def test_a_pinned_contract_hash_that_is_not_a_digest_is_refused(tmp_path):
    """It could never match, so every night would report a contract change."""
    text = SOURCES.replace(f"sha256:{ROLES}", "sha256:abc")

    with pytest.raises(ValueError, match=r"usa_wa\.roles.*contract_hash"):
        load_subscription(_write(tmp_path, text), source="usa_wa")


def test_a_file_without_the_named_source_is_refused(tmp_path):
    """An empty subscription would pull nothing and exit 0 every night."""
    with pytest.raises(ValueError, match="usa_wa"):
        load_subscription(_write(tmp_path, SOURCES.replace("usa_wa", "renamed")), source="usa_wa")


def test_the_committed_sources_file_pins_its_datasets():
    """The nightly reads this file with no flags; a bad pin must fail here first.

    Which datasets it pins is `test_project.py`'s parity test, in the tier that
    can import the mapping registry.
    """
    subscription = load_subscription()

    assert subscription.pins
