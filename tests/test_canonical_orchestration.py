"""End-to-end orchestration for canonical resolve+store (#765 Stage 2 trigger).

Uses a real temp DB (album + tracks + source IDs) and an INJECTED fetcher, so
the DB gathering + persistence are exercised for real without live APIs.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.metadata.canonical_resolver import (
    default_fetch_tracklist,
    provider_artist_id,
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
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name, thumb_url) VALUES ('art1', 'Imagine Dragons', 'http://artist.jpg')")
    cur.execute(
        "INSERT INTO albums (id, title, artist_id, thumb_url, spotify_album_id) "
        "VALUES ('alb1', 'Evolve', 'art1', 'http://album.jpg', 'sp1')"
    )
    for i in range(11):
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration) "
            "VALUES (?, 'alb1', 'art1', ?, ?, ?)",
            (f"t{i}", f"Song {i+1}", i + 1, 180_000 + i * 10_000),
        )
    conn.commit()
    conn.close()

    out = resolve_and_store_canonical_for_album(
        db, "alb1", fetch_tracklist=lambda s, a: STD, source_priority=["spotify"],
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


def test_local_soul_id_is_not_a_hydrabase_artist_id():
    assert provider_artist_id(
        {'artist_id': 'local', 'artist_hydrabase_id': 'locally-derived-soul-id'},
        'hydrabase',
    ) == ''


def test_malformed_stored_musicbrainz_artist_id_uses_name_search():
    assert provider_artist_id(
        {'artist_id': 'local', 'artist_musicbrainz_id': '4QLGuDRbRtIIDoMyH9Zgg8'},
        'musicbrainz',
    ) == ''


def test_alternate_lookup_uses_provider_artist_id_not_local_key(tmp_path, monkeypatch):
    import core.metadata.canonical_resolver as resolver

    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="spotify_album")
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE artists SET musicbrainz_id = ?, spotify_artist_id = ? WHERE id = 'art1'",
            ("8e3bb70b-cb74-4296-9bbd-ae00050fd4ca", "spotify_artist"),
        )
        conn.execute(
            "UPDATE albums SET musicbrainz_release_id = ? WHERE id = ?",
            ("musicbrainz_release", album_id),
        )

    calls = []

    def alternates(source, album, **kwargs):
        calls.append((source, kwargs["artist_id"], kwargs["artist_name"]))
        return []

    monkeypatch.setattr(resolver, "default_fetch_alternates", alternates)
    resolve_and_store_canonical_for_album(
        db, album_id, fetch_tracklist=lambda source, album: DLX,
        source_priority=["spotify", "musicbrainz"], mode="best_fit", store=False,
        min_score=0.99,
    )
    assert calls == [
        ("spotify", "spotify_artist", "Imagine Dragons"),
        ("musicbrainz", "8e3bb70b-cb74-4296-9bbd-ae00050fd4ca", "Imagine Dragons"),
    ]


def test_alternate_lookup_without_provider_id_searches_by_name(tmp_path, monkeypatch):
    import core.metadata.canonical_resolver as resolver

    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db, spotify="spotify_album")
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE albums SET musicbrainz_release_id = ? WHERE id = ?",
            ("musicbrainz_release", album_id),
        )

    calls = []

    def alternates(source, album, **kwargs):
        calls.append((source, kwargs["artist_id"], kwargs["artist_name"]))
        return []

    monkeypatch.setattr(resolver, "default_fetch_alternates", alternates)
    resolve_and_store_canonical_for_album(
        db, album_id, fetch_tracklist=lambda source, album: DLX,
        source_priority=["spotify", "musicbrainz"], mode="best_fit", store=False,
        min_score=0.99,
    )
    assert calls == [
        ("spotify", "", "Imagine Dragons"),
        ("musicbrainz", "", "Imagine Dragons"),
    ]


def test_canonical_misfit_never_sends_invalid_artist_id_to_musicbrainz(tmp_path, monkeypatch):
    import core.metadata.album_tracks as album_tracks

    db = MusicDatabase(str(tmp_path / "m.db"))
    album_id = _seed(db)
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE albums SET musicbrainz_release_id = ? WHERE id = ?",
            ("linked-release", album_id),
        )
        conn.execute(
            "UPDATE artists SET musicbrainz_id = ? WHERE id = 'art1'",
            ("4QLGuDRbRtIIDoMyH9Zgg8",),
        )

    calls = []
    artist_mbid = "8e3bb70b-cb74-4296-9bbd-ae00050fd4ca"

    def search_artists(name, **kwargs):
        calls.append(("search", name))
        return [{"id": artist_mbid, "name": name}]

    def get_artist_albums(artist_id, **kwargs):
        calls.append(("albums", artist_id))
        return [{"id": "standard-release", "name": "Evolve (Standard)"}]

    client = SimpleNamespace(search_artists=search_artists,
                             get_artist_albums=get_artist_albums)
    monkeypatch.setattr(album_tracks.metadata_registry, "get_client_for_source",
                        lambda source: client)
    monkeypatch.setattr("core.metadata_service.get_album_tracks_for_source",
                        lambda source, album: STD if album == "standard-release" else None)

    result = resolve_and_store_canonical_for_album(
        db, album_id, fetch_tracklist=lambda source, album: DLX,
        source_priority=["musicbrainz"], min_score=0.99, store=False,
    )
    assert result and result["album_id"] == "standard-release"
    assert calls == [("search", "Imagine Dragons"), ("albums", artist_mbid)]


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
