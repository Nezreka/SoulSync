"""Daily Mixes split seeds into artists you have and discovery. lib2_artists
also holds featured guests and watch-only artists; counting those as owned
seeded mixes with clusters that had nothing to play and kept their songs out
of every mix's discovery side (139ba4181 fixed the same for the taste map)."""

from __future__ import annotations

from core.personalized.daily_mixes import _norm, _seed_edges
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_only_artists_you_have_music_by_are_owned(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    lib2_seed.track(conn, "Taylor Swift", "folklore", "cardigan")
    lib2_seed.artist(conn, "Bon Iver")                 # a featured guest, no music
    lib2_seed.track(conn, "Phoebe Bridgers", "Punisher", "Kyoto", owned=False)
    conn.commit()
    conn.close()

    _similars, owned = _seed_edges(db, ["Taylor Swift"], 1)

    assert _norm("Taylor Swift") in owned
    assert _norm("Bon Iver") not in owned
    assert _norm("Phoebe Bridgers") not in owned
