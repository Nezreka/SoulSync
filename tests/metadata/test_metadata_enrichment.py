from collections import OrderedDict
import types
import sqlite3

import pytest
import requests

from core.metadata import enrichment as me
from core.metadata import artwork as ma
from core.metadata import source as ms
from core.metadata import album_mbid_cache as _album_mbid_cache


@pytest.fixture(autouse=True)
def _isolate_persistent_album_mbid_cache(monkeypatch):
    """The MB release lookup in `core/metadata/source.py` consults the
    persistent album-MBID cache (`core/metadata/album_mbid_cache.py`)
    before calling MusicBrainz. Tests in this file pin per-test MB
    call counts and in-memory cache state — they shouldn't get
    bypassed by leftover persistent rows from other tests sharing the
    same SQLite database.

    Easiest fix: monkeypatch the persistent cache to be a no-op for
    these tests. They focus on the in-memory layer + MB call shape;
    the persistent layer has its own dedicated tests at
    `tests/metadata/test_album_mbid_cache.py`.
    """
    monkeypatch.setattr(_album_mbid_cache, 'lookup', lambda *a, **kw: None)
    monkeypatch.setattr(_album_mbid_cache, 'record', lambda *a, **kw: False)


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _FakeTag:
    def __init__(self, kind, **kwargs):
        self.kind = kind
        self.kwargs = kwargs


class _FakeID3Tags:
    def __init__(self):
        self.added = []

    def add(self, frame):
        self.added.append(frame)

    def clear(self):
        self.added.clear()

    def getall(self, _key):
        return []

    def keys(self):
        return [frame.kind for frame in self.added]

    def __len__(self):
        return len(self.added)


class _FakeAudio:
    def __init__(self):
        self.tags = _FakeID3Tags()
        self.save_calls = []
        self.clear_pictures_calls = 0

    def clear_pictures(self):
        self.clear_pictures_calls += 1

    def add_tags(self):
        self.tags = _FakeID3Tags()

    def save(self, **kwargs):
        self.save_calls.append(kwargs)


class _FakeResponse:
    def __init__(self, payload, content_type="image/jpeg"):
        self._payload = payload
        self._content_type = content_type

    def read(self):
        return self._payload

    def info(self):
        return types.SimpleNamespace(get_content_type=lambda: self._content_type)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FileDB:
    def __init__(self, db_path):
        self.db_path = db_path

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _fake_symbols(audio):
    def _tag_factory(kind):
        return lambda **kwargs: _FakeTag(kind, **kwargs)

    return types.SimpleNamespace(
        File=lambda _path: audio,
        APEv2=type("FakeAPEv2", (), {}),
        APENoHeaderError=Exception,
        FLAC=type("FakeFLAC", (), {}),
        Picture=type("FakePicture", (), {"__init__": lambda self: None}),
        ID3=_FakeID3Tags,
        APIC=_tag_factory("APIC"),
        TBPM=_tag_factory("TBPM"),
        TCOP=_tag_factory("TCOP"),
        TDOR=_tag_factory("TDOR"),
        TDRC=_tag_factory("TDRC"),
        TCON=_tag_factory("TCON"),
        TIT2=_tag_factory("TIT2"),
        TALB=_tag_factory("TALB"),
        TPE1=_tag_factory("TPE1"),
        TPE2=_tag_factory("TPE2"),
        TPOS=_tag_factory("TPOS"),
        TPUB=_tag_factory("TPUB"),
        TRCK=_tag_factory("TRCK"),
        TSRC=_tag_factory("TSRC"),
        TXXX=_tag_factory("TXXX"),
        UFID=_tag_factory("UFID"),
        TMED=_tag_factory("TMED"),
        MP4=type("FakeMP4", (), {}),
        MP4Cover=types.SimpleNamespace(FORMAT_JPEG=1, FORMAT_PNG=2, __call__=lambda *args, **kwargs: ("cover", args, kwargs)),
        MP4FreeForm=lambda data: ("freeform", data),
        OggVorbis=type("FakeOggVorbis", (), {}),
        OggOpus=None,
    )


def test_extract_source_metadata_keeps_neutral_fields_and_skips_itunes_fallback_for_non_itunes_sources(monkeypatch):
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config({"file_organization.collab_artist_mode": "first"}))
    monkeypatch.setattr(ms, "get_itunes_client", lambda: (_ for _ in ()).throw(AssertionError("itunes fallback should not run for non-itunes sources")))

    context = {
        "source": "spotify",
        "artist": {"name": "Artist One & Artist Two", "id": "123", "genres": ["rock", "indie"]},
        "album": {
            "name": "Album One",
            "total_tracks": 12,
            "release_date": "2024-01-02",
            "images": [{"url": "https://img.example/album.jpg"}],
        },
        "track_info": {
            "artists": [{"name": "Artist One"}],
            "_source": "spotify",
            "track_number": 3,
            "disc_number": 2,
            "total_tracks": 12,
        },
        "original_search_result": {
            "title": "Song One",
            "artists": [{"name": "Artist One"}],
            "clean_title": "Song One",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
            "disc_number": 2,
            "duration_ms": 180000,
        },
    }

    metadata = me.extract_source_metadata(
        context,
        context["artist"],
        {"is_album": True, "album_name": "Album One", "track_number": 3, "disc_number": 2, "album_image_url": "https://img.example/album.jpg"},
    )

    assert metadata["source"] == "spotify"
    assert metadata["title"] == "Song One"
    assert metadata["artist"] == "Artist One"
    assert metadata["album_artist"] == "Artist One & Artist Two"
    assert metadata["album"] == "Album One"
    assert metadata["track_number"] == 3
    assert metadata["total_tracks"] == 12
    assert metadata["disc_number"] == 2
    assert metadata["album_art_url"] == "https://img.example/album.jpg"


def test_embed_source_ids_uses_current_source_ids_and_legacy_fallback(monkeypatch):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(ms, "get_database", lambda: None)

    current_metadata = {
        "source": "deezer",
        "source_track_id": "dz-track",
        "source_artist_id": "dz-artist",
        "source_album_id": "dz-album",
        "title": "Song One",
        "artist": "Artist One",
        "album_artist": "Artist One",
        "album": "Album One",
    }
    me.embed_source_ids(audio, current_metadata, context={"track_info": {}, "original_search_result": {}})

    current_descs = [frame.kwargs.get("desc") for frame in audio.tags.added if frame.kind == "TXXX"]
    assert "DEEZER_TRACK_ID" in current_descs
    assert "DEEZER_ARTIST_ID" in current_descs

    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: symbols)

    legacy_metadata = {
        "source": "",
        "spotify_track_id": "sp-track",
        "spotify_artist_id": "sp-artist",
        "spotify_album_id": "sp-album",
        "itunes_track_id": "it-track",
        "itunes_artist_id": "it-artist",
        "itunes_album_id": "it-album",
        "title": "Song One",
        "artist": "Artist One",
        "album_artist": "Artist One",
        "album": "Album One",
    }
    me.embed_source_ids(audio, legacy_metadata, context={"track_info": {}, "original_search_result": {}})

    legacy_descs = [frame.kwargs.get("desc") for frame in audio.tags.added if frame.kind == "TXXX"]
    assert "SPOTIFY_TRACK_ID" in legacy_descs
    assert "SPOTIFY_ARTIST_ID" in legacy_descs
    assert "SPOTIFY_ALBUM_ID" in legacy_descs
    assert "ITUNES_TRACK_ID" in legacy_descs
    assert "ITUNES_ARTIST_ID" in legacy_descs
    assert "ITUNES_ALBUM_ID" in legacy_descs


def test_embed_source_ids_skips_disabled_source_specific_tags(monkeypatch):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)

    monkeypatch.setattr(
        ms,
        "get_config_manager",
        lambda: _Config({"deezer.embed_tags": False}),
    )
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(ms, "get_database", lambda: None)

    metadata = {
        "source": "deezer",
        "source_track_id": "dz-track",
        "source_artist_id": "dz-artist",
        "source_album_id": "dz-album",
        "title": "Song One",
        "artist": "Artist One",
        "album_artist": "Artist One",
        "album": "Album One",
    }

    me.embed_source_ids(audio, metadata, context={"track_info": {}, "original_search_result": {}})

    assert audio.tags.added == []


def test_embed_source_ids_writes_musicbrainz_release_year_and_updates_album_year(tmp_path, monkeypatch):
    db_path = tmp_path / "music.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE albums (id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, year INTEGER)")
    conn.execute("INSERT INTO artists (id, name) VALUES (?, ?)", ("artist-1", "Artist One"))
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, year) VALUES (?, ?, ?, ?)",
        ("album-1", "artist-1", "Album One", None),
    )
    conn.commit()
    conn.close()

    class _FakeMBClient:
        def get_recording(self, mbid, includes=None):
            return {
                "isrcs": ["ISRC-123"],
                "genres": [{"name": "Post Rock", "count": 10}],
            }

        def get_release(self, mbid, includes=None):
            return {
                "release-group": {
                    "id": "rg-1",
                    "primary-type": "album",
                    "first-release-date": "2021-09-17",
                },
                "artist-credit": [{"artist": {"id": "artist-mb-1"}}],
                "status": "Official",
                "country": "US",
                "barcode": "1234567890",
                "media": [{"format": "CD", "tracks": [{"position": 1, "id": "reltrack-1", "recording": {"id": "rec-1"}}]}],
                "label-info": [{"catalog-number": "CAT-1"}],
                "text-representation": {"script": "Latn"},
                "asin": "ASIN1",
            }

    class _FakeMBService:
        def __init__(self):
            self.mb_client = _FakeMBClient()

        def match_recording(self, title, artist):
            return {"mbid": "rec-mbid"}

        def match_artist(self, artist):
            return {"mbid": "artist-mbid"}

        def match_release(self, album, artist):
            return {"mbid": "release-mbid"}

    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    runtime = types.SimpleNamespace(mb_worker=types.SimpleNamespace(mb_service=_FakeMBService()))

    monkeypatch.setattr(
        ms,
        "get_config_manager",
        lambda: _Config(
            {
                "metadata_enhancement.enabled": True,
                "metadata_enhancement.embed_album_art": False,
                "metadata_enhancement.tags.write_multi_artist": False,
                "musicbrainz.embed_tags": True,
            }
        ),
    )
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(ms, "get_database", lambda: _FileDB(str(db_path)))

    metadata = {
        "source": "musicbrainz",
        "title": "Song One",
        "artist": "Artist One",
        "album_artist": "Artist One",
        "album": "Album One",
        "track_number": 1,
        "total_tracks": 12,
        "disc_number": 1,
    }

    me.embed_source_ids(audio, metadata, context={"track_info": {}, "original_search_result": {}}, runtime=runtime)

    assert metadata["musicbrainz_release_id"] == "release-mbid"
    assert metadata["date"] == "2021"
    assert any(frame.kind == "TDRC" for frame in audio.tags.added)
    assert any(frame.kind == "TSRC" for frame in audio.tags.added)

    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        """
        SELECT albums.year
        FROM albums
        JOIN artists ON artists.id = albums.artist_id
        WHERE albums.title = ? AND artists.name = ?
        """,
        ("Album One", "Artist One"),
    ).fetchone()
    check.close()

    assert row["year"] == 2021


def test_musicbrainz_release_lookup_failure_does_not_poison_cache(monkeypatch):
    class _FakeMBClient:
        def get_release(self, mbid, includes=None):
            return {}

        def get_artist(self, mbid, includes=None):
            return {}

    class _FakeMBService:
        def __init__(self):
            self.release_calls = 0
            self.mb_client = _FakeMBClient()

        def match_recording(self, title, artist):
            return None

        def match_artist(self, artist):
            return {"mbid": "artist-mbid"}

        def match_release(self, album, artist):
            self.release_calls += 1
            if self.release_calls == 1:
                raise requests.RequestException("temporary MusicBrainz outage")
            return {"mbid": "release-mbid"}

    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config({"musicbrainz.embed_tags": True}))
    monkeypatch.setattr(ms, "mb_release_cache", {})
    monkeypatch.setattr(ms, "mb_release_detail_cache", {})

    service = _FakeMBService()
    runtime = types.SimpleNamespace(mb_worker=types.SimpleNamespace(mb_service=service))
    pp = {
        "id_tags": {},
        "track_title": "Song One",
        "artist_name": "Artist One",
        "batch_artist_name": "Artist One",
        "metadata": {"album": "Album One"},
        "recording_mbid": None,
        "artist_mbid": None,
        "release_mbid": "",
        "mb_genres": [],
        "isrc": None,
        "deezer_bpm": None,
        "deezer_isrc": None,
        "audiodb_mood": None,
        "audiodb_style": None,
        "audiodb_genre": None,
        "tidal_isrc": None,
        "tidal_copyright": None,
        "qobuz_isrc": None,
        "qobuz_copyright": None,
        "qobuz_label": None,
        "lastfm_tags": [],
        "lastfm_url": None,
        "genius_url": None,
        "release_year": None,
    }
    metadata = {"album": "Album One", "artist": "Artist One"}

    ms._process_musicbrainz_source(pp, metadata, _Config({"musicbrainz.embed_tags": True}), runtime, "Song One", "Artist One")
    assert service.release_calls == 1
    assert ms.mb_release_cache == {}

    poisoned_norm_key = (ms.normalize_album_cache_key("Album One"), "artist one")
    poisoned_exact_key = ("album one", "artist one")
    ms.mb_release_cache[poisoned_norm_key] = ""
    ms.mb_release_cache[poisoned_exact_key] = ""

    ms._process_musicbrainz_source(pp, metadata, _Config({"musicbrainz.embed_tags": True}), runtime, "Song One", "Artist One")
    assert service.release_calls == 2


def test_source_processors_do_not_swallow_programmer_errors():
    class _BoomClient:
        def search_track(self, artist_name, track_title):
            raise ValueError("boom")

    runtime = types.SimpleNamespace(deezer_worker=types.SimpleNamespace(client=_BoomClient()))
    pp = {
        "id_tags": {},
        "batch_artist_name": "Artist One",
        "release_year": None,
    }
    metadata = {"album": "Album One"}

    with pytest.raises(ValueError):
        ms._process_deezer_source(pp, metadata, _Config({"deezer.embed_tags": True}), runtime, "Song One", "Artist One")


def test_musicbrainz_caches_evict_oldest_entries():
    release_cache = OrderedDict()
    detail_cache = OrderedDict()

    ms._bounded_cache_set(release_cache, ("album-1", "artist"), "release-1", 2)
    ms._bounded_cache_set(release_cache, ("album-2", "artist"), "release-2", 2)
    assert list(release_cache.keys()) == [("album-1", "artist"), ("album-2", "artist")]

    assert ms._bounded_cache_get(release_cache, ("album-1", "artist")) == "release-1"
    ms._bounded_cache_set(release_cache, ("album-3", "artist"), "release-3", 2)
    assert list(release_cache.keys()) == [("album-1", "artist"), ("album-3", "artist")]

    ms._bounded_cache_set(detail_cache, "release-1", {"title": "One"}, 1)
    ms._bounded_cache_set(detail_cache, "release-2", {"title": "Two"}, 1)
    assert list(detail_cache.keys()) == ["release-2"]


def test_enhance_file_metadata_forwards_runtime_to_source_embedding(monkeypatch):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    seen = {}
    runtime = types.SimpleNamespace(marker="runtime")

    monkeypatch.setattr(me, "get_config_manager", lambda: _Config(
        {
            "metadata_enhancement.enabled": True,
            "metadata_enhancement.embed_album_art": False,
            "metadata_enhancement.tags.write_multi_artist": False,
        }
    ))
    monkeypatch.setattr(me, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(me, "strip_all_non_audio_tags", lambda file_path: {"apev2_stripped": False, "apev2_tag_count": 0})
    monkeypatch.setattr(
        me,
        "extract_source_metadata",
        lambda context, artist, album_info: {
            "source": "deezer",
            "source_track_id": "dz-track",
            "source_artist_id": "dz-artist",
            "source_album_id": "dz-album",
            "title": "Song One",
            "artist": "Artist One",
            "album_artist": "Artist One",
            "album": "Album One",
            "track_number": 3,
            "total_tracks": 12,
            "disc_number": 2,
            "date": "2024",
            "genre": "Rock",
        },
    )
    monkeypatch.setattr(
        me,
        "embed_source_ids",
        lambda audio_file, metadata, context, runtime=None: seen.setdefault("runtime", runtime),
    )
    monkeypatch.setattr(me, "embed_album_art_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(me, "verify_metadata_written", lambda file_path: True)

    result = me.enhance_file_metadata(
        "song.flac",
        {"_audio_quality": ""},
        {"name": "Artist One"},
        {},
        runtime=runtime,
    )

    assert result is True
    assert seen["runtime"] is runtime


def test_enhance_file_metadata_writes_tags_and_propagates_release_id(monkeypatch):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    strip_calls = []
    verify_calls = []

    monkeypatch.setattr(me, "get_config_manager", lambda: _Config(
        {
            "metadata_enhancement.enabled": True,
            "metadata_enhancement.embed_album_art": False,
            "metadata_enhancement.tags.write_multi_artist": False,
        }
    ))
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(me, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(ms, "get_mutagen_symbols", lambda: symbols)
    monkeypatch.setattr(ms, "get_database", lambda: None)
    monkeypatch.setattr(me, "strip_all_non_audio_tags", lambda file_path: strip_calls.append(file_path) or {"apev2_stripped": False, "apev2_tag_count": 0})
    monkeypatch.setattr(
        me,
        "extract_source_metadata",
        lambda context, artist, album_info: {
            "source": "deezer",
            "source_track_id": "dz-track",
            "source_artist_id": "dz-artist",
            "source_album_id": "dz-album",
            "title": "Song One",
            "artist": "Artist One",
            "album_artist": "Artist One",
            "album": "Album One",
            "track_number": 3,
            "total_tracks": 12,
            "disc_number": 2,
            "date": "2024",
            "genre": "Rock",
            "musicbrainz_release_id": "mb-release-1",
        },
    )
    monkeypatch.setattr(me, "embed_album_art_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(me, "verify_metadata_written", lambda file_path: verify_calls.append(file_path) or True)

    album_info = {}
    result = me.enhance_file_metadata(
        "song.flac",
        {"_audio_quality": ""},
        {"name": "Artist One"},
        album_info,
    )

    assert result is True
    assert strip_calls == ["song.flac"]
    assert verify_calls == ["song.flac"]
    assert audio.clear_pictures_calls == 1
    assert len(audio.save_calls) == 2
    assert album_info["musicbrainz_release_id"] == "mb-release-1"
    assert any(frame.kind == "TIT2" for frame in audio.tags.added)
    assert any(frame.kind == "TPE1" for frame in audio.tags.added)
    assert any(frame.kind == "TXXX" and frame.kwargs.get("desc") == "DEEZER_TRACK_ID" for frame in audio.tags.added)


def test_download_cover_art_uses_album_context_image_url(tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "get_config_manager", lambda: _Config(
        {
            "metadata_enhancement.cover_art_download": True,
            "metadata_enhancement.prefer_caa_art": False,
        }
    ))

    monkeypatch.setattr(ma.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse(b"cover-bytes"))

    target_dir = tmp_path / "Album One"
    target_dir.mkdir()

    ma.download_cover_art(
        {},
        str(target_dir),
        {"album": {"image_url": "https://img.example/album.jpg"}},
    )

    cover_path = target_dir / "cover.jpg"
    assert cover_path.exists()
    assert cover_path.read_bytes() == b"cover-bytes"


# ---------------------------------------------------------------------------
# MusicBrainz genre fallback chain (recording → release → artist)
# ---------------------------------------------------------------------------

def _build_mb_genre_test(monkeypatch, *, recording_genres, release_genres, artist_genres):
    """Helper: assemble a fake MB stack with configurable genres at each tier."""
    class _FakeMBClient:
        def __init__(self):
            self.artist_calls = 0
            self.release_calls = 0

        def get_recording(self, mbid, includes=None):
            return {"isrcs": [], "genres": list(recording_genres)}

        def get_release(self, mbid, includes=None):
            self.release_calls += 1
            return {"genres": list(release_genres), "media": []}

        def get_artist(self, mbid, includes=None):
            self.artist_calls += 1
            return {"genres": list(artist_genres)}

    class _FakeMBService:
        def __init__(self):
            self.mb_client = _FakeMBClient()

        def match_recording(self, t, a):
            return {"mbid": "rec-mbid"}

        def match_artist(self, a):
            return {"mbid": "artist-mbid"}

        def match_release(self, album, artist):
            return {"mbid": "release-mbid"}

    service = _FakeMBService()
    monkeypatch.setattr(ms, "get_config_manager", lambda: _Config({"musicbrainz.embed_tags": True}))
    monkeypatch.setattr(ms, "mb_release_cache", {})
    monkeypatch.setattr(ms, "mb_release_detail_cache", {})

    runtime = types.SimpleNamespace(mb_worker=types.SimpleNamespace(mb_service=service))
    pp = {
        "id_tags": {}, "track_title": "T", "artist_name": "A", "batch_artist_name": "A",
        "metadata": {"album": "Alb"}, "recording_mbid": None, "artist_mbid": None,
        "release_mbid": "", "mb_genres": [], "isrc": None,
        "deezer_bpm": None, "deezer_isrc": None,
        "audiodb_mood": None, "audiodb_style": None, "audiodb_genre": None,
        "tidal_isrc": None, "tidal_copyright": None,
        "qobuz_isrc": None, "qobuz_copyright": None, "qobuz_label": None,
        "lastfm_tags": [], "lastfm_url": None, "genius_url": None, "release_year": None,
    }
    return pp, service, runtime


def test_mb_genre_recording_used_when_present(monkeypatch):
    pp, service, runtime = _build_mb_genre_test(
        monkeypatch,
        recording_genres=[{"name": "Rock", "count": 5}],
        release_genres=[{"name": "Pop", "count": 10}],
        artist_genres=[{"name": "Jazz", "count": 20}],
    )
    ms._process_musicbrainz_source(pp, {"album": "Alb"}, _Config({"musicbrainz.embed_tags": True}),
                                    runtime, "T", "A")
    assert pp["mb_genres"] == ["Rock"]
    # Release/artist genre lookups not consulted because recording had genres
    assert service.mb_client.artist_calls == 0


def test_mb_genre_falls_back_to_release_when_recording_empty(monkeypatch):
    pp, service, runtime = _build_mb_genre_test(
        monkeypatch,
        recording_genres=[],
        release_genres=[{"name": "Pop", "count": 10}, {"name": "Indie", "count": 3}],
        artist_genres=[{"name": "Jazz", "count": 20}],
    )
    ms._process_musicbrainz_source(pp, {"album": "Alb"}, _Config({"musicbrainz.embed_tags": True}),
                                    runtime, "T", "A")
    # Sorted by count desc: Pop (10) before Indie (3)
    assert pp["mb_genres"] == ["Pop", "Indie"]
    # Artist not consulted because release had genres
    assert service.mb_client.artist_calls == 0


def test_mb_genre_falls_back_to_artist_when_recording_and_release_empty(monkeypatch):
    pp, service, runtime = _build_mb_genre_test(
        monkeypatch,
        recording_genres=[],
        release_genres=[],
        artist_genres=[{"name": "Jazz", "count": 20}, {"name": "Fusion", "count": 5}],
    )
    ms._process_musicbrainz_source(pp, {"album": "Alb"}, _Config({"musicbrainz.embed_tags": True}),
                                    runtime, "T", "A")
    assert pp["mb_genres"] == ["Jazz", "Fusion"]
    assert service.mb_client.artist_calls == 1


def test_mb_genre_all_empty_returns_empty(monkeypatch):
    pp, service, runtime = _build_mb_genre_test(
        monkeypatch,
        recording_genres=[],
        release_genres=[],
        artist_genres=[],
    )
    ms._process_musicbrainz_source(pp, {"album": "Alb"}, _Config({"musicbrainz.embed_tags": True}),
                                    runtime, "T", "A")
    assert pp["mb_genres"] == []
