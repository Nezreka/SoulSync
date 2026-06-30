"""Popularity backfill sweep — fills via the cascade, terminates, sentinels the unfillable."""

from __future__ import annotations

from core.discovery.popularity_backfill import get_state, run_backfill


class _FakeDB:
    """In-memory similar_artists popularity: 0 = missing, >0 = filled, -1 = tried/none."""

    def __init__(self, names):
        self.pop = {n: 0 for n in names}
        self.updates = 0

    def count_similar_artists_missing_popularity(self, profile_id=1):
        return sum(1 for v in self.pop.values() if v == 0)

    def get_similar_artists_missing_popularity(self, limit=50, profile_id=1):
        return [{"name": n, "spotify_id": "sp_" + n, "deezer_id": None}
                for n, v in self.pop.items() if v == 0][:limit]

    def update_similar_artist_popularity(self, name, popularity, profile_id=1):
        self.pop[name] = int(round(float(popularity)))
        self.updates += 1
        return 1


class _SpotifyFree:
    """Returns followers for everyone except 'Ghost' (nothing finds it)."""

    def get_artist(self, artist_id):
        return None if artist_id == "sp_Ghost" else {"followers": {"total": 1_000_000}}


def test_backfill_fills_sentinels_and_terminates():
    db = _FakeDB(["Real1", "Real2", "Ghost"])
    filled = run_backfill(db, spotify_free=_SpotifyFree(), sleep_s=0, batch_size=10)

    assert filled == 2                       # the two resolvable artists
    assert db.pop["Real1"] > 0 and db.pop["Real2"] > 0
    assert db.pop["Ghost"] == -1             # unfillable -> sentinel, won't be retried
    assert db.count_similar_artists_missing_popularity() == 0   # nothing left at 0 -> terminated

    st = get_state()
    assert st["running"] is False and st["done"] == 3 and st["filled"] == 2


def test_backfill_respects_max_artists():
    db = _FakeDB([f"A{i}" for i in range(20)])
    run_backfill(db, spotify_free=_SpotifyFree(), sleep_s=0, batch_size=5, max_artists=7)
    st = get_state()
    assert st["done"] == 7                    # stopped early at the cap
    assert db.count_similar_artists_missing_popularity() == 13


def test_backfill_no_clients_sentinels_everything():
    db = _FakeDB(["X", "Y"])
    filled = run_backfill(db, sleep_s=0)      # no sources at all
    assert filled == 0
    assert db.pop["X"] == -1 and db.pop["Y"] == -1
