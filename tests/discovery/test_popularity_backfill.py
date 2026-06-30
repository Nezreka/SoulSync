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


class _SpotifyFreeObscure:
    """An artist so small its follower count normalizes to 0 (below the floor)."""

    def get_artist(self, artist_id):
        return {"followers": {"total": 50}}


class _FakeDBBrokenUpdate(_FakeDB):
    """Simulates updates that never stick (DB error) — popularity stays 0."""

    def update_similar_artist_popularity(self, name, popularity, profile_id=1):
        self.updates += 1
        return 0  # "failed" — self.pop unchanged, so the row stays "missing"


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


def test_backfill_obscure_artist_stored_as_floor_not_zero():
    # P0 regression: a found value that normalizes to 0 must NOT be written as 0 (which the query
    # reads as "missing" -> re-fetch forever). It's floored to >= 1, so the row is done.
    db = _FakeDB(["Tiny"])
    run_backfill(db, spotify_free=_SpotifyFreeObscure(), sleep_s=0)
    assert db.pop["Tiny"] >= 1
    assert db.count_similar_artists_missing_popularity() == 0


def test_backfill_terminates_even_when_updates_dont_stick():
    # P0 regression: if updates never persist, the same rows keep coming back from the query. The
    # seen-guard must bail instead of looping forever (which would hammer the APIs continuously).
    db = _FakeDBBrokenUpdate(["A", "B", "C"])
    run_backfill(db, spotify_free=_SpotifyFree(), sleep_s=0, batch_size=2)
    assert get_state()["running"] is False
    assert db.updates <= 3            # each row attempted at most once, then it stops — no infinite loop
