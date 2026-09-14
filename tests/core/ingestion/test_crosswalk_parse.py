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
    "kind,usa_wa_id,pm_id,span_key\n"
    "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
    "organization,01KVGJM842V1GPTX81N1MN33HT,01KVGJMD9S41QSBSNMM82897JC,\n"
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
        "kind,usa_wa_id,pm_id,span_key\n"
        "person,0189d0c3f0d47c8ba0c2ad1e6b0f9a11,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
    )
    with pytest.raises(AnchorFormatError, match="not a base32 ULID"):
        parse_anchors(csv)


def test_rejects_an_id_using_a_letter_crockford_excludes():
    """I, L, O and U are not Crockford base32 — a ULID containing one is corrupt."""
    csv = (
        "kind,usa_wa_id,pm_id,span_key\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HI,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
    )
    with pytest.raises(AnchorFormatError, match="not a base32 ULID"):
        parse_anchors(csv)


def test_rejects_an_unknown_kind():
    csv = (
        "kind,usa_wa_id,pm_id,span_key\n"
        "jurisdiction,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
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
        "kind,usa_wa_id,pm_id,span_key\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KVGJM842V1GPTX81N1MN33HT,\n"
    )
    with pytest.raises(AnchorFormatError, match="duplicate"):
        parse_anchors(csv)


def test_the_same_producer_id_under_a_different_kind_is_not_a_duplicate():
    """The key is (kind, id): usa-wa mints per-kind, so the pair is the identity."""
    csv = (
        "kind,usa_wa_id,pm_id,span_key\n"
        "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
        "role,01KV6T7RTS5PVF1HB94T5X23HY,01KVGJM842V1GPTX81N1MN33HT,\n"
    )
    assert len(parse_anchors(csv)) == 2


def test_verify_digest_accepts_the_published_prefixed_form():
    """The catalog states `"hash": "sha256:6d51…"`, which is what the puller hands over."""
    verify_digest(
        b"kind,usa_wa_id,pm_id\n",
        "sha256:f3b3d96d77da4cd2b38d77c4a1d51d518cb7bcb47614bf776dbd68c88d307f86",
    )


def test_verify_digest_refuses_an_algorithm_it_does_not_compute():
    """Comparing a sha512 digest as opaque text would fail as a content mismatch.

    That reads as 'this file is corrupt' when the truth is 'nobody checked it'.
    """
    with pytest.raises(AnchorFormatError, match="unsupported digest algorithm"):
        verify_digest(b"anything", "sha512:" + "0" * 128)


# --- #525: the span_key column -----------------------------------------------------
# usa-wa publishes `span_key` on every kind=assignment anchor (pm_anchors schema
# 1.7.0): the assignment's key in the published dataset, which carries no
# assignment id. An empty key is usa-wa saying the anchor has no published row.

KEYED_HEADER = "kind,usa_wa_id,pm_id,span_key\n"
A_ID, A_PM = "01KWWWMAT8NS3YAQFA1YY5S61B", "01KWWX4M1EP9GHTPT4JR3W045V"
SPAN = "01KWWWM9E94EYQK3JC2X36G0CJ|committee-member-role:31640|committee|31640|2025-26"


def test_an_assignment_anchor_keys_on_its_span_key():
    [anchor] = parse_anchors(KEYED_HEADER + f"assignment,{A_ID},{A_PM},{SPAN}\n")

    assert anchor == Anchor("assignment", A_ID, A_PM, SPAN)
    assert anchor.key == SPAN


def test_an_empty_span_key_keeps_the_anchor_on_its_usa_wa_id():
    """usa-wa writes the empty key quoted; either way it means no published row."""
    csv = KEYED_HEADER + f'assignment,{A_ID},{A_PM},""\n' + f"assignment,{A_PM},{A_ID},\n"

    anchors = parse_anchors(csv)

    assert [(a.span_key, a.key) for a in anchors] == [(None, A_ID), (None, A_PM)]


def test_a_person_keys_on_its_usa_wa_id():
    [anchor] = parse_anchors(
        KEYED_HEADER + "person,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A,\n"
    )

    assert anchor.span_key is None and anchor.key == "01KV6T7RTS5PVF1HB94T5X23HY"


def test_refuses_the_keyless_header_that_predates_span_key():
    """After the re-key a keyless export would move every assignment back to its ULID."""
    csv = "kind,usa_wa_id,pm_id\nperson,01KV6T7RTS5PVF1HB94T5X23HY,01KV6SW78XKRQDWC4MPWNEPQ5A\n"

    with pytest.raises(AnchorFormatError, match="predates span_key"):
        parse_anchors(csv)


def test_refuses_a_span_key_on_a_row_that_is_not_an_assignment():
    csv = KEYED_HEADER + f"person,{A_ID},{A_PM},{SPAN}\n"

    with pytest.raises(AnchorFormatError, match="only an assignment"):
        parse_anchors(csv)


@pytest.mark.parametrize(
    "key",
    [
        "01KWWWM9E94EYQK3JC2X36G0CJ|committee-member-role:31640|committee|2025-26",  # four
        f"{SPAN}|extra",  # six
        "not-a-registry-ulid|committee-member-role:31640|committee|31640|2025-26",
    ],
)
def test_refuses_a_malformed_span_key(key):
    """Five `|`-separated fields, the person's registry ULID first."""
    with pytest.raises(AnchorFormatError, match="five .*-separated fields"):
        parse_anchors(KEYED_HEADER + f"assignment,{A_ID},{A_PM},{key}\n")


def test_refuses_two_anchors_with_one_span_key():
    """Two anchors on one key would re-key onto the same row: last one wins, silently."""
    csv = KEYED_HEADER + f"assignment,{A_ID},{A_PM},{SPAN}\nassignment,{A_PM},{A_ID},{SPAN}\n"

    with pytest.raises(AnchorFormatError, match="duplicate span_key"):
        parse_anchors(csv)
