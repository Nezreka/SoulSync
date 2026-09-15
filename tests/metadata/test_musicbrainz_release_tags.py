from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen._vorbis import VCommentDict

from core.metadata import source as ms
from core.metadata.common import get_mutagen_symbols
from core.metadata.musicbrainz_tags import release_tags, write_tag, selected_release_id


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def release(rid="chosen"):
    credit = [{"name": "Madonna", "artist": {"id": "artist", "name": "Madonna", "sort-name": "Madonna"}}]
    return {"id": rid, "date": "2005-11-14", "title": "Confessions", "artist-credit": credit,
            "status": "Official", "release-group": {"id": "group", "first-release-date": "2005-11-11", "primary-type": "Album"},
            "label-info": [{"label": {"name": "Warner Bros. Records"}, "catalog-number": "CAT"}],
            "media": [{"position": 1, "format": "Digital Media", "tracks": [
                {"id": rid + "-track", "position": 1, "title": "Hung Up", "recording": {"id": rid + "-recording"}}]}]}


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(ms, "mb_release_cache", {("confessions", "madonna"): "wrong"})
    monkeypatch.setattr(ms, "mb_release_detail_cache", {})
    client = SimpleNamespace(get_release=Mock(side_effect=lambda rid, **kw: release(rid)),
                             get_recording=Mock(return_value={"isrcs": ["US1", "US2", "US3"], "artist-credit": release()["artist-credit"]}))
    service = SimpleNamespace(mb_client=client, match_release=Mock(side_effect=AssertionError("must not select another edition")),
                              match_recording=Mock(side_effect=AssertionError("must use release recording")),
                              match_artist=Mock(side_effect=AssertionError("must use credits")))
    return SimpleNamespace(mb_worker=SimpleNamespace(mb_service=service))


def process(runtime, rid="chosen"):
    metadata = {"musicbrainz_release_id": rid, "title": "Hung Up", "album": "Confessions", "artist": "Madonna", "date": "2005-01-01", "track_number": 1, "disc_number": 1}
    state = ms._blank_post_process_state()
    ms._process_musicbrainz_source(state, metadata, Config(), runtime, "Hung Up", "Madonna")
    return metadata, state


def test_selected_edition_bypasses_name_cache_and_other_edition(runtime):
    for rid in ("edition-a", "edition-b"):
        metadata, state = process(runtime, rid)
        assert state["id_tags"]["MUSICBRAINZ_RELEASE_ID"] == rid
        assert state["id_tags"]["MUSICBRAINZ_RELEASETRACKID"] == rid + "-track"
        assert state["id_tags"]["MUSICBRAINZ_RECORDING_ID"] == rid + "-recording"
        assert state["mb_isrcs"] == ["US1", "US2", "US3"]
    assert ms.mb_release_cache == {("confessions", "madonna"): "wrong"}


def test_unavailable_selected_edition_does_not_fallback(runtime):
    runtime.mb_worker.mb_service.mb_client.get_release.return_value = None
    runtime.mb_worker.mb_service.mb_client.get_release.side_effect = None
    _, state = process(runtime)
    assert "MUSICBRAINZ_RELEASETRACKID" not in state["id_tags"]
    runtime.mb_worker.mb_service.match_release.assert_not_called()


@pytest.mark.parametrize("kind", ["mp3", "flac", "opus", "m4a"])
def test_picard_tags_and_date_are_written_in_each_format(runtime, kind, tmp_path):
    symbols = get_mutagen_symbols()
    if kind == "mp3":
        audio = SimpleNamespace(tags=ID3())
    else:
        cls = {"flac": FLAC, "opus": OggOpus, "m4a": MP4}[kind]
        audio = cls.__new__(cls)
        audio.tags = {} if kind == "m4a" else VCommentDict()
    metadata, state = process(runtime)
    ms._write_embedded_metadata(audio, metadata, state, Config(), symbols)
    assert metadata["date"] == "2005-11-14"
    if kind == "mp3":
        path = tmp_path / "tags.id3"
        audio.tags.save(path, v2_version=4)
        tags = ID3(path)
        assert str(tags["TDRC"]) == "2005-11-14"
        assert str(tags["TDOR"]) == "2005-11-11"
        assert tags["TSRC"].text == ["US1", "US2", "US3"]
        assert tags["TPUB"].text == ["Warner Bros. Records"]
        assert tags["TSOP"].text == ["Madonna"]
        assert tags["TSO2"].text == ["Madonna"]
        assert tags["TXXX:Artists"].text == ["Madonna"]
        assert tags["TXXX:originalyear"].text == ["2005"]
    elif kind == "m4a":
        assert audio["\xa9day"] == ["2005-11-14"]
        assert audio["soar"] == ["Madonna"]
        assert audio["soaa"] == ["Madonna"]
        assert audio["----:com.apple.iTunes:ISRC"] == [b"US1", b"US2", b"US3"]
    else:
        assert audio["DATE"] == ["2005-11-14"]
        assert audio["ORIGINALDATE"] == ["2005-11-11"]
        assert audio["ORIGINALYEAR"] == ["2005"]
        assert audio["LABEL"] == ["Warner Bros. Records"]
        assert audio["ARTISTS"] == ["Madonna"]
        assert audio["ISRC"] == ["US1", "US2", "US3"]
        assert audio["RELEASESTATUS"] == ["official"]
        assert audio["RELEASETYPE"] == ["album"]


def test_preserves_date_precision():
    for date in ("2005", "2005-11", "2005-11-14"):
        data = release()
        data["date"] = date
        assert release_tags(data)["DATE"] == date


def test_import_context_preserves_concrete_release(monkeypatch):
    from core.imports.album import build_album_import_context
    monkeypatch.setattr(ms, "get_config_manager", lambda: Config())
    album = {"id": "group", "musicbrainz_release_id": "chosen", "name": "Confessions", "source": "musicbrainz", "artists": [{"id": "artist", "name": "Madonna"}]}
    context = build_album_import_context(album, {"id": "rec", "name": "Hung Up"}, artist_context=album["artists"][0], source="musicbrainz")
    metadata = ms.extract_source_metadata(context, album["artists"][0], {})
    assert metadata["musicbrainz_release_id"] == "chosen"
    assert not selected_release_id({"id": "group", "external_urls": {"musicbrainz": "https://musicbrainz.org/release-group/group"}})


def test_consistency_does_not_adopt_or_reselect_explicit_release(monkeypatch, tmp_path):
    from core import album_consistency as ac
    path = tmp_path / "track.mp3"
    path.touch()
    audio = SimpleNamespace(tags=ID3())
    monkeypatch.setattr(ac, "MutagenFile", lambda *a, **kw: audio)
    monkeypatch.setattr(ac, "_atomic_save", lambda a: True)
    monkeypatch.setattr(ac, "_adopt_album_tags_from_siblings", Mock(side_effect=AssertionError("must not adopt different edition")))
    monkeypatch.setattr(ac, "_resolve_album_release", Mock(side_effect=AssertionError("must not reselect")))
    service = SimpleNamespace(mb_client=SimpleNamespace(get_release=Mock(return_value=release())))
    result = ac.run_album_consistency([{"path": str(path), "track_number": 1, "disc_number": 1, "title": "Hung Up"}], "Confessions", "Madonna", service, release_mbid="chosen")
    assert result["success"]
    assert str(audio.tags["TXXX:MusicBrainz Album Id"]) == "chosen"
    assert str(audio.tags["TDRC"]) == "2005-11-14"


def test_new_fields_obey_tag_toggles(runtime):
    metadata, state = process(runtime)
    audio = SimpleNamespace(tags=ID3())
    cfg = Config(**{"musicbrainz.tags.release_date": False, "musicbrainz.tags.label": False, "musicbrainz.tags.isrc": False})
    ms._write_embedded_metadata(audio, metadata, state, cfg, get_mutagen_symbols())
    assert "TPUB" not in audio.tags
    assert "TSRC" not in audio.tags
    assert "TDRC" not in audio.tags
    assert metadata["date"] == "2005-01-01"


@pytest.mark.parametrize("extension,codec", [("flac", "flac"), ("opus", "libopus"), ("m4a", "aac"), ("mp3", "libmp3lame")])
def test_tags_survive_real_audio_save(runtime, tmp_path, extension, codec):
    import os
    import shutil
    import subprocess
    from mutagen import File
    from core.metadata.common import save_audio_file
    ffmpeg = shutil.which("ffmpeg") or os.environ.get("SOULSYNC_TEST_FFMPEG")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    path = tmp_path / ("song." + extension)
    output_path = str(path)
    if os.name == "posix" and ffmpeg.lower().endswith(".exe"):
        output_path = subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.1", "-c:a", codec, output_path], check=True, capture_output=True)
    audio = File(path)
    if audio.tags is None:
        audio.add_tags()
    metadata, state = process(runtime)
    ms._write_embedded_metadata(audio, metadata, state, Config(), get_mutagen_symbols())
    assert save_audio_file(audio, get_mutagen_symbols())
    saved = File(path)
    if extension == "mp3":
        assert str(saved.tags["TDRC"]) == "2005-11-14"
        assert saved.tags["TSRC"].text == ["US1", "US2", "US3"]
    elif extension == "m4a":
        assert saved["\xa9day"] == ["2005-11-14"]
        assert saved["----:com.apple.iTunes:ISRC"] == [b"US1", b"US2", b"US3"]
    else:
        assert saved["DATE"] == ["2005-11-14"]
        assert saved["ISRC"] == ["US1", "US2", "US3"]
        assert saved["MUSICBRAINZ_ALBUMID"] == ["chosen"]


def test_consistency_unavailable_pin_leaves_file_untouched(monkeypatch, tmp_path):
    from core import album_consistency as ac
    path = tmp_path / "original.mp3"
    path.write_bytes(b"unchanged")
    service = SimpleNamespace(mb_client=SimpleNamespace(get_release=Mock(return_value=None)))
    result = ac.run_album_consistency([{"path": str(path)}], "Album", "Artist", service, release_mbid="chosen")
    assert not result["success"]
    assert path.read_bytes() == b"unchanged"


def test_legacy_alias_is_removed_and_canonical_id_read_by_consistency():
    from core import album_consistency as ac
    audio = FLAC.__new__(FLAC)
    audio.tags = VCommentDict()
    audio["MUSICBRAINZ_RELEASE_ID"] = ["old"]
    write_tag(audio, "MUSICBRAINZ_RELEASE_ID", "new", get_mutagen_symbols())
    assert "MUSICBRAINZ_RELEASE_ID" not in audio
    assert ac._read_tag_from_file(audio, "MUSICBRAINZ_RELEASE_ID") == "new"


def test_selected_release_does_not_assign_wrong_song_at_same_position(runtime):
    runtime.mb_worker.mb_service.mb_client.get_release.side_effect = None
    wrong = release()
    wrong["media"][0]["tracks"][0]["title"] = "Another Song"
    runtime.mb_worker.mb_service.mb_client.get_release.return_value = wrong
    _, state = process(runtime)
    assert "MUSICBRAINZ_RELEASETRACKID" not in state["id_tags"]
    assert "MUSICBRAINZ_RECORDING_ID" not in state["id_tags"]
    runtime.mb_worker.mb_service.mb_client.get_recording.assert_not_called()


def test_title_check_preserves_unicode_and_version_names():
    from core.metadata.musicbrainz_tags import track_matches_title
    assert track_matches_title("東京", {"title": "東京"})
    assert not track_matches_title("東京", {"title": "大阪"})
    assert not track_matches_title("Song (Live)", {"title": "Song (Remix)"})


def test_selected_release_recovers_after_transient_lookup_failure(runtime):
    client = runtime.mb_worker.mb_service.mb_client
    client.get_release.side_effect = [None, release()]
    _, first = process(runtime)
    _, second = process(runtime)
    assert "MUSICBRAINZ_RELEASETRACKID" not in first["id_tags"]
    assert second["id_tags"]["MUSICBRAINZ_RELEASETRACKID"] == "chosen-track"
