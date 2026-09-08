"""Parsing and integrity of a producer anchor export (#495).

The export is the seed for PM's row scope (#490 addendum gap A): every later
diff is scoped to the rows named here, so a mis-parsed file is not a bad batch,
it is a silently wrong scope. These tests hold the two rules that make the file
self-describing — the header is a contract, and the digest is checked before the
rows are believed.
"""

import pytest

from src.core.ingestion.crosswalk import (
    Anchor,
    AnchorFormatError,
    parse_anchors,
    verify_digest,
)

GOOD_CSV = (
    "kind,usa_wa_id,pm_id\n"
    "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
    "organization,01KVGJM842V1GPTX81N1MN33HT,01KVGJMD9S41QSBSNMM82897JC\n"
)


def test_parses_each_row_into_an_anchor():
    assert parse_anchors(GOOD_CSV) == [
        Anchor("person", "01KV6T7RTS5PVF1HB94T5X23HY", "01KV6SW78XKRQDWC4MPWNEPQ5A"),
        Anchor("organization", "01KVGJM842V1GPTX81N1MN33HT", "01KVGJMD9S41QSBSNMM82897JC"),
    ]


def test_rejects_a_uuid_hex_id():
    """usa-wa#324 pins Crockford base32; a UUID-hex id is a different id space.

    It is 32 hex characters where a ULID is 26 Crockford ones, so it cannot
    match any PM row — accepting it would put an unresolvable anchor in the
    scope and report it as merely 'missing'.
    """
    csv = (
        "kind,usa_wa_id,pm_id\nperson,0189d0c3f0d47c8ba0c2ad1e6b0f9a11,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
    )
    with pytest.raises(AnchorFormatError, match="not a base32 ULID"):
        parse_anchors(csv)


def test_rejects_an_id_using_a_letter_crockford_excludes():
    """I, L, O and U are not Crockford base32 — a ULID containing one is corrupt."""
    csv = "kind,usa_wa_id,pm_id\nperson,01KV6T7RTS5PVF1HB94T5X23HI,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
    with pytest.raises(AnchorFormatError, match="not a base32 ULID"):
        parse_anchors(csv)


def test_rejects_an_unknown_kind():
    csv = (
        "kind,usa_wa_id,pm_id\njurisdiction,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
    )
    with pytest.raises(AnchorFormatError, match="unknown kind"):
        parse_anchors(csv)


def test_rejects_a_renamed_header():
    """The header is the contract; a renamed column means the export changed shape."""
    csv = "kind,producer_id,pm_id\nperson,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
    with pytest.raises(AnchorFormatError, match="header"):
        parse_anchors(csv)


def test_rejects_an_empty_file_rather_than_returning_no_anchors():
    """An empty export would scope the applier to nothing and look like success."""
    with pytest.raises(AnchorFormatError, match="header"):
        parse_anchors("")


def test_verify_digest_accepts_the_matching_sha256():
    data = b"kind,usa_wa_id,pm_id\n"
    verify_digest(data, "f3b3d96d77da4cd2b38d77c4a1d51d518cb7bcb47614bf776dbd68c88d307f86")


def test_verify_digest_rejects_a_mismatch():
    """A truncated download is the failure this catches; it must not be a warning."""
    with pytest.raises(AnchorFormatError, match="digest"):
        verify_digest(b"anything", "0" * 64)


def test_rejects_the_same_producer_id_twice():
    """Two rows for one producer id would upsert last-wins with nothing said.

    It is the mirror of the collision check — that one catches two producer ids
    landing on one PM row; this catches one producer id claiming two PM rows —
    and only this half is invisible, because `ON CONFLICT DO UPDATE` resolves it
    silently while the report still counts both.
    """
    csv = (
        "kind,usa_wa_id,pm_id\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KVGJM842V1GPTX81N1MN33HT\n"
    )
    with pytest.raises(AnchorFormatError, match="duplicate"):
        parse_anchors(csv)


def test_the_same_producer_id_under_a_different_kind_is_not_a_duplicate():
    """The key is (kind, id): usa-wa mints per-kind, so the pair is the identity."""
    csv = (
        "kind,usa_wa_id,pm_id\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"
        "role,01KV6T7RTS5PVF1HB94T5X23HY,01KVGJM842V1GPTX81N1MN33HT\n"
    )
    assert len(parse_anchors(csv)) == 2
