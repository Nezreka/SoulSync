"""#1607 (cremonies): deezer downloads came out with no genre. deezer keeps
genre on the album (/album/{id} genres.data), never on the track or artist, and
the tag writer only read the artist's genres, always [] for deezer."""

from __future__ import annotations

import pytest

from core.metadata import enrichment as me
from core.metadata import registry
from core.metadata import source as ms
from core.metadata.deezer_genres import album_genre_names

MOANA_RAW = {
    "id": 14582002, "title": "Moana (Deluxe)", "nb_tracks": 59, "release_date": "2016-11-18",
    "record_type": "album", "artist": {"id": 1545788, "name": "Lin-Manuel Miranda"},
    "genres": {"data": [{"id": 173, "name": "Films/Games"}, {"id": 174, "name": "Film Scores"}]},
}


class _Config:
    def __init__(self, values=None):
        self.values = {"file_organization.collab_artist_mode": "first", **(values or {})}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeDeezer:
    def __init__(self, album):
        self.album = album
        self.calls = []

    def get_album_metadata(self, album_id, include_tracks=True):
        self.calls.append((album_id, include_tracks))
        return self.album


def _context(source="deezer", album=None):
    return {
        "source": source,
        "artist": {"name": "Auli'i Cravalho", "id": "9", "genres": []},
        "album": album or {"id": "14582002", "name": "Moana (Deluxe)", "total_tracks": 59,
                           "artists": [{"name": "Lin-Manuel Miranda"}]},
        "track_info": {"id": "136340808", "_source": source, "track_number": 4,
                       "artists": [{"name": "Auli'i Cravalho"}]},
        "original_search_result": {"title": "How Far I'll Go", "clean_title": "How Far I'll Go",
                                   "artists": [{"name": "Auli'i Cravalho"}]},
    }


def _extract(ctx):
    return me.extract_source_metadata(
        ctx, ctx["artist"],
        {"is_album": True, "album_name": "Moana (Deluxe)", "track_number": 4, "disc_number": 1},
    )


def test_album_genre_names_reads_genres_data():
    assert album_genre_names(MOANA_RAW) == ["Films/Games", "Film Scores"]
    assert album_genre_names({"genres": {"data": [{"id": 0, "name": "All"}]}}) == []
    assert album_genre_names({}) == [] and album_genre_names(None) == []


def test_deezer_album_result_carries_its_genres():
    from core.deezer_client import DeezerClient
    album = DeezerClient._build_album_result(None, MOANA_RAW, "14582002", include_tracks=False)
    assert album["genres"] == ["Films/Games", "Film Scores"]


def test_deezer_download_gets_the_albums_genre(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    fake = _FakeDeezer({"genres": ["Films/Games", "Film Scores"]})
    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: fake)

    metadata = _extract(_context())

    assert metadata["genre"] == "Films/Games, Film Scores"
    assert fake.calls == [("14582002", False)]   # album only, no track list


def test_album_genres_in_the_context_need_no_lookup(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(registry, "get_deezer_client",
                        lambda *a, **k: pytest.fail("context already had the genres"))
    ctx = _context(album={"id": "14582002", "name": "Moana (Deluxe)", "total_tracks": 59,
                          "genres": ["Film Scores"]})
    assert _extract(ctx)["genre"] == "Film Scores"


def test_artist_genres_still_win(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(registry, "get_deezer_client",
                        lambda *a, **k: pytest.fail("artist genres are enough"))
    ctx = _context()
    ctx["artist"]["genres"] = ["pop"]
    assert _extract(ctx)["genre"] == "pop"


def test_other_sources_never_ask_deezer(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(registry, "get_deezer_client",
                        lambda *a, **k: pytest.fail("not a deezer download"))
    assert "genre" not in _extract(_context(source="spotify"))


def test_whitelist_still_filters_album_genres(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config(
        {"genre_whitelist.enabled": True, "genre_whitelist.genres": ["Film Scores"]}))
    monkeypatch.setattr(registry, "get_deezer_client",
                        lambda *a, **k: _FakeDeezer({"genres": ["Films/Games", "Film Scores"]}))
    assert _extract(_context())["genre"] == "Film Scores"


def test_a_failed_lookup_leaves_no_genre(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())

    class _Boom:
        def get_album_metadata(self, *_a, **_k):
            raise RuntimeError("deezer down")

    monkeypatch.setattr(registry, "get_deezer_client", lambda *a, **k: _Boom())
    assert "genre" not in _extract(_context())
