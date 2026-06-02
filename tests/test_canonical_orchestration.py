"""End-to-end orchestration for canonical resolve+store (#765 Stage 2 trigger).

Uses a real temp DB (album + tracks + source IDs) and an INJECTED fetcher, so
the DB gathering + persistence are exercised for real without live APIs.
"""

from __future__ import annotations

from core.metadata.canonical_resolver import (
    default_fetch_tracklist,
    resolve_and_store_canonical_for_album,
)
from database.music_database import MusicDatabase

STD = [{"duration_ms": 180_000 + i * 10_000, "title": f"Song {i+1}", "track_number": i + 1} for i in range(11)]
DLX = STD + [{"duration_ms": 320_000 + i * 10_000, "title": f"Bonus {i+1}", "track_number": 12 + i} for i in range(6)]


def _seed(db, *, spotify=None, deezer=None, n_files=11):
    """Insert an album (with given source IDs) + n_files tracks whose
    durations/titles match the STANDARD release."""
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('art1', 'Imagine Dragons')")
    cur.execute(
        "INSERT INTO albums (id, title, artist_id, spotify_album_id, deezer_id) "
        "VALUES ('alb1', 'Evolve', 'art1', ?, ?)",
        (spotify, deezer),
    )
    for i in range(n_files):
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration) "
            "VALUES (?, 'alb1', 'art1', ?, ?, ?)",
            (f"t{i}", f"Song {i+1}", i + 1, 180_000 + i * 10_000),
        )
    conn.commit()
    conn.close()
    return "alb1"


def test_resolve_and_store_picks_best_fit_and_persists(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="sp_deluxe", deezer="dz_std")  # 11 files

    table = {("spotify", "sp_deluxe"): DLX, ("deezer", "dz_std"): STD}
    out = resolve_and_store_canonical_for_album(
        db, album_id,
        fetch_tracklist=lambda s, a: table.get((s, a)),
        source_priority=["spotify", "deezer"],
    )
    # Deezer's standard matches the 11 files better than Spotify's deluxe.
    assert out["source"] == "deezer" and out["album_id"] == "dz_std"
    # ...and it was persisted.
    stored = db.get_album_canonical(album_id)
    assert stored["source"] == "deezer" and stored["album_id"] == "dz_std"


def test_resolve_returns_none_when_album_has_no_source_ids(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify=None, deezer=None)
    out = resolve_and_store_canonical_for_album(
        db, album_id, fetch_tracklist=lambda s, a: STD, source_priority=["spotify"],
    )
    assert out is None
    assert db.get_album_canonical(album_id) is None


def test_resolve_returns_none_for_missing_album(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    out = resolve_and_store_canonical_for_album(
        db, "does-not-exist", fetch_tracklist=lambda s, a: STD, source_priority=["spotify"],
    )
    assert out is None


# ── default_fetch_tracklist normalization (no DB / no live API) ────────────

def test_default_fetcher_normalizes_dict_items(monkeypatch):
    import core.metadata_service as ms
    monkeypatch.setattr(
        ms, "get_album_tracks_for_source",
        lambda s, a: [{"name": "A", "track_number": 1, "duration_ms": 200000},
                      {"title": "B", "track_number": 2, "duration": 210}],  # seconds
        raising=False,
    )
    out = default_fetch_tracklist("spotify", "x")
    assert out[0] == {"title": "A", "track_number": 1, "duration_ms": 200000}
    assert out[1] == {"title": "B", "track_number": 2, "duration_ms": 210_000}  # sec->ms


def test_default_fetcher_handles_failure(monkeypatch):
    import core.metadata_service as ms
    monkeypatch.setattr(
        ms, "get_album_tracks_for_source",
        lambda s, a: (_ for _ in ()).throw(RuntimeError("boom")), raising=False,
    )
    assert default_fetch_tracklist("spotify", "x") is None
