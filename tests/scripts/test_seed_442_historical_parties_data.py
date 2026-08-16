"""Constant-only checks on the #442 party seed — no database, no marker.

Split out of ``test_seed_442_historical_parties.py`` (CR round 2, finding 8):
that module is ``-m integration``, so a convention violation in ``PARTIES`` stayed
invisible on the default suite. Nothing here touches a connection, so it runs for
free on every `pytest` and fails fast.
"""

from scripts import seed_442_historical_parties as seed


def test_party_values_are_bare_lowercase_slugs():
    """#270's value convention: no ``wa-`` prefix; the type already scopes to WA."""
    for party in seed.PARTIES:
        assert party.party_value == party.party_value.lower()
        assert not party.party_value.startswith("wa-")
        assert " " not in party.party_value
