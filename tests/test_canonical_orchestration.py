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


def _seed(db, *, spotify=None, deezer=None, n_files=11, artist_image=None,
          album_image=None):
    """A catalogue album (with given source IDs) + n_files tracks whose
    durations/titles match the STANDARD release. Returns its catalogue id."""
    from tests.support.catalogue_seed import seed_album, seed_artist, seed_track
    conn = db._get_connection()
    artist = seed_artist(conn, server_id='art1', name='Imagine Dragons',
                         image_url=artist_image)
    album = seed_album(conn, server_id='alb1', title='Evolve', artist_id=artist,
                       image_url=album_image)
    conn.execute(
        "UPDATE lib2_albums SET spotify_id=?,"
        "       external_ids=CASE WHEN ? IS NULL THEN external_ids"
        "                         ELSE json_set(external_ids,'$.deezer',?) END"
        " WHERE id=?", (spotify, deezer, deezer, album))
    for i in range(n_files):
        seed_track(conn, server_id=f"t{i}", title=f"Song {i+1}", album_id=album,
                   artist_id=artist, track_number=i + 1,
                   duration=180_000 + i * 10_000)
    conn.commit()
    conn.close()
    return album


def test_resolve_and_store_picks_best_fit_and_persists(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="sp_deluxe", deezer="dz_std")  # 11 files

    table = {("spotify", "sp_deluxe"): DLX, ("deezer", "dz_std"): STD}
    out = resolve_and_store_canonical_for_album(
        db, album_id,
        fetch_tracklist=lambda s, a: table.get((s, a)),
        source_priority=["spotify", "deezer"],
        mode="best_fit",
    )
    # best_fit: Deezer's standard matches the 11 files better than Spotify's deluxe.
    assert out["source"] == "deezer" and out["album_id"] == "dz_std"
    # ...and it was persisted.
    stored = db.get_album_canonical(album_id)
    assert stored["source"] == "deezer" and stored["album_id"] == "dz_std"


def test_default_mode_prefers_active_source(tmp_path):
    # Same setup, but default (active_preferred) mode: primary = spotify, whose
    # deluxe still clears the floor -> pinned, even though deezer fits better.
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="sp_deluxe", deezer="dz_std")
    table = {("spotify", "sp_deluxe"): DLX, ("deezer", "dz_std"): STD}
    out = resolve_and_store_canonical_for_album(
        db, album_id,
        fetch_tracklist=lambda s, a: table.get((s, a)),
        source_priority=["spotify", "deezer"],  # default mode
    )
    assert out["source"] == "spotify"  # active source preferred


def test_result_includes_artist_and_album_context(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="sp1", artist_image='http://artist.jpg',
                     album_image='http://album.jpg')

    out = resolve_and_store_canonical_for_album(
        db, album_id, fetch_tracklist=lambda s, a: STD, source_priority=["spotify"],
    )
    assert out["artist_name"] == "Imagine Dragons"
    assert out["album_thumb_url"] == "http://album.jpg"
    assert out["artist_thumb_url"] == "http://artist.jpg"
    # free context: db track count, linked sources, and both title lists
    assert out["db_track_count"] == 11
    assert out["linked_sources"] == {"spotify": "sp1"}
    assert out["file_track_titles"][0] == "Song 1" and len(out["file_track_titles"]) == 11
    assert "Song 1" in out["release_track_titles"]


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
