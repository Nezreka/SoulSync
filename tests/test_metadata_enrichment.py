import logging
import types

import pytest

from core import metadata_enrichment as me


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


def test_extract_source_metadata_keeps_neutral_fields_and_skips_itunes_fallback_for_non_itunes_sources():
    class _ItunesClient:
        def __init__(self):
            self.called = False

        def resolve_primary_artist(self, artist_id):
            self.called = True
            raise AssertionError("itunes fallback should not run for non-itunes sources")

    runtime = types.SimpleNamespace(
        logger=logging.getLogger("test.metadata_enrichment"),
        config_manager=_Config({"file_organization.collab_artist_mode": "first"}),
        itunes_enrichment_worker=types.SimpleNamespace(client=_ItunesClient()),
    )

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
        runtime=runtime,
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
    assert runtime.itunes_enrichment_worker.client.called is False


def test_embed_source_ids_uses_current_source_ids_and_legacy_fallback(monkeypatch):
    runtime = types.SimpleNamespace(
        logger=logging.getLogger("test.metadata_enrichment"),
        config_manager=_Config(),
        mb_worker=None,
        deezer_worker=None,
        audiodb_worker=None,
        tidal_client=None,
        qobuz_enrichment_worker=None,
        lastfm_worker=None,
        genius_worker=None,
        itunes_enrichment_worker=None,
        get_database=lambda: None,
    )

    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    monkeypatch.setattr(me, "_get_mutagen_symbols", lambda runtime=None: symbols)

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
    me.embed_source_ids(audio, current_metadata, context={"track_info": {}, "original_search_result": {}}, runtime=runtime)

    current_descs = [frame.kwargs.get("desc") for frame in audio.tags.added if frame.kind == "TXXX"]
    assert "DEEZER_TRACK_ID" in current_descs
    assert "DEEZER_ARTIST_ID" in current_descs

    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    monkeypatch.setattr(me, "_get_mutagen_symbols", lambda runtime=None: symbols)

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
    me.embed_source_ids(audio, legacy_metadata, context={"track_info": {}, "original_search_result": {}}, runtime=runtime)

    legacy_descs = [frame.kwargs.get("desc") for frame in audio.tags.added if frame.kind == "TXXX"]
    assert "SPOTIFY_TRACK_ID" in legacy_descs
    assert "SPOTIFY_ARTIST_ID" in legacy_descs
    assert "SPOTIFY_ALBUM_ID" in legacy_descs
    assert "ITUNES_TRACK_ID" in legacy_descs
    assert "ITUNES_ARTIST_ID" in legacy_descs
    assert "ITUNES_ALBUM_ID" in legacy_descs


def test_enhance_file_metadata_writes_tags_and_propagates_release_id(monkeypatch):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    runtime = types.SimpleNamespace(
        logger=logging.getLogger("test.metadata_enrichment"),
        config_manager=_Config(
            {
                "metadata_enhancement.enabled": True,
                "metadata_enhancement.embed_album_art": False,
                "metadata_enhancement.tags.write_multi_artist": False,
            }
        ),
        mb_worker=None,
        deezer_worker=None,
        audiodb_worker=None,
        tidal_client=None,
        qobuz_enrichment_worker=None,
        lastfm_worker=None,
        genius_worker=None,
        itunes_enrichment_worker=None,
        get_database=lambda: None,
    )

    strip_calls = []
    verify_calls = []

    monkeypatch.setattr(me, "_get_mutagen_symbols", lambda runtime=None: symbols)
    monkeypatch.setattr(me, "_strip_all_non_audio_tags", lambda file_path, runtime=None: strip_calls.append(file_path) or {"apev2_stripped": False, "apev2_tag_count": 0})
    monkeypatch.setattr(
        me,
        "extract_source_metadata",
        lambda context, artist, album_info, runtime=None: {
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
    monkeypatch.setattr(me, "_verify_metadata_written", lambda file_path, runtime=None: verify_calls.append(file_path) or True)

    album_info = {}
    result = me.enhance_file_metadata(
        "song.flac",
        {"_audio_quality": ""},
        {"name": "Artist One"},
        album_info,
        runtime=runtime,
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
    runtime = types.SimpleNamespace(
        logger=logging.getLogger("test.metadata_enrichment"),
        config_manager=_Config(
            {
                "metadata_enhancement.cover_art_download": True,
                "metadata_enhancement.prefer_caa_art": False,
            }
        ),
    )

    monkeypatch.setattr(me.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse(b"cover-bytes"))

    target_dir = tmp_path / "Album One"
    target_dir.mkdir()

    me.download_cover_art(
        {},
        str(target_dir),
        {"album": {"image_url": "https://img.example/album.jpg"}},
        runtime=runtime,
    )

    cover_path = target_dir / "cover.jpg"
    assert cover_path.exists()
    assert cover_path.read_bytes() == b"cover-bytes"
