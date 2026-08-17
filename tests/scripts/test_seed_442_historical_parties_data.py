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


def test_party_values_are_unique():
    """A repeated value would make the second run resolve ``ambiguous_identifier``."""
    values = [p.party_value for p in seed.PARTIES]
    assert len(values) == len(set(values))


def test_six_parties_are_minted():
    """Seven tokens, six Orgs — ``Cit.`` is deliberately never minted."""
    assert len(seed.PARTIES) == 6


def test_citizen_is_never_minted():
    """``Cit.`` was not a state party.

    Municipal-archive records show "Citizens Party" / "Citizen Nonpartisan" used
    as hyper-local ballot labels, and the two 1907 Jefferson County members
    elected on a Citizen's ticket identified as a Republican and a Democrat once
    seated. That puts it under the rule already applied to ``Independent``: a
    ballot label is not an organization. Minting one later is cheap; un-minting
    is not, so the absence is asserted rather than left to memory.
    """
    values = {p.party_value for p in seed.PARTIES}
    assert not values & {"citizen", "citizens", "cit"}
    names = " ".join(p.name for p in seed.PARTIES).lower()
    assert "citizen" not in names
