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
