"""The watchlist reconcile's index answers exactly what the pairwise matcher did."""

import itertools
import random

from core.library2.monitor_sync import _artist_identity_matches, _WatchlistIndex


def test_the_index_agrees_with_the_pairwise_match_everywhere():
    rng = random.Random(1062)
    names = ["Björk", "bjork", "BJÖRK ", "Air", "AIR", "Daft Punk", "", None]
    ids = [{}, {"spotify": "sp1"}, {"spotify": "sp2"}, {"spotify": " sp1 "},
           {"musicbrainz": "mb1"}, {"spotify": "sp1", "musicbrainz": "mb2"},
           {"deezer": 7}, {"spotify": ""}]
    for _ in range(300):
        entries = [{"name": rng.choice(names), "provider_ids": rng.choice(ids)}
                   for _ in range(rng.randint(0, 6))]
        index = _WatchlistIndex(entries)
        for name, candidate in itertools.product(names, ids):
            brute = any(_artist_identity_matches(name, candidate, e["name"], e["provider_ids"])
                        for e in entries)
            assert index.matches(name, candidate) == brute, (name, candidate, entries)


def test_a_contradicting_id_beats_a_same_name():
    index = _WatchlistIndex([{"name": "Air", "provider_ids": {"spotify": "sp-french"}}])
    assert index.matches("Air", {"spotify": "sp-other"}) is False
    assert index.matches("Air", {"musicbrainz": "mb"}) is True
    assert index.matches("Somebody", {"spotify": "sp-french"}) is True


def test_with_artists_a_name_needs_one_artist_and_an_unclaimed_entry():
    """review 5 W2-WF-2: the reconcile's narrowed name fallback"""
    entries = [{"name": "Nirvana", "provider_ids": {"spotify": "sp-us"}},
               {"name": "Air", "provider_ids": {"spotify": "sp-air"}}]
    artists = [(1, "Nirvana", {"spotify": "sp-us"}),
               (2, "Nirvana", {"musicbrainz": "mb-uk"}),
               (3, "Air", {"deezer": "dz-air"})]
    index = _WatchlistIndex(entries, artists)
    assert index.matches("Nirvana", {"spotify": "sp-us"}, 1) is True
    assert index.matches("Nirvana", {"musicbrainz": "mb-uk"}, 2) is False
    assert index.matches("Air", {"deezer": "dz-air"}, 3) is True
    # without an identity it is the pairwise answer, as before
    assert index.matches("Nirvana", {"musicbrainz": "mb-uk"}) is True
    assert index.matching_entries("Air", {"deezer": "dz-air"}, 3) == [1]
