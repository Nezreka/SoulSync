"""the languages in a video file (Thibs): audio and embedded subtitles, read
by ffprobe or sent by jellyfin, kept across server scans, shown, and usable in
the naming templates. a file nobody has read yet is never renamed on a guess."""

import os

import pytest

from core.video import mediainfo, organization
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _probe_json(audio=("jpn", "eng"), subs=("eng",)):
    streams = [{"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "hevc"}]
    streams += [{"codec_type": "audio", "codec_name": "aac", "channels": 6,
                 "tags": {"language": a}} for a in audio]
    streams += [{"codec_type": "subtitle", "tags": {"language": s}} for s in subs]
    return {"streams": streams, "format": {"duration": "1440"}}


def _file_row(db, movie_id):
    conn = db._get_connection()
    try:
        return dict(conn.execute(
            "SELECT audio_languages, subtitle_languages, audio_channels FROM media_files "
            "WHERE movie_id=?", (movie_id,)).fetchone())
    finally:
        conn.close()


def test_ffprobe_reads_every_track_language_once():
    info = mediainfo.parse_ffprobe(_probe_json(audio=("jpn", "eng", "und", "jpn"),
                                               subs=("eng", "ger")))
    assert info["audio_languages"] == ["ja", "en"]     # 'und' says nothing, jpn once
    assert info["subtitle_languages"] == ["en", "de"]
    assert info["audio_channels"] == 6


def test_a_server_that_sends_languages_is_believed(db):
    mid = db.upsert_movie("jellyfin", {"server_id": "j1", "title": "Spirited Away", "file": {
        "relative_path": "/m/Spirited Away.mkv", "size_bytes": 900,
        "audio_languages": ["ja", "en"], "subtitle_languages": []}})
    assert _file_row(db, mid)["audio_languages"] == "ja,en"
    assert _file_row(db, mid)["subtitle_languages"] == ""     # looked at, none


def test_a_probe_outlives_the_server_rescanning_the_file(db):
    # plex sends no stream data: the scan writes the row without languages
    item = {"server_id": "p1", "title": "Spirited Away",
            "file": {"relative_path": "/data/movies/Spirited Away.mkv", "size_bytes": 900}}
    mid = db.upsert_movie("plex", item)
    assert _file_row(db, mid)["audio_languages"] is None      # not read yet

    # soulsync reads it, on the path it sees the file under
    info = mediainfo.parse_ffprobe(_probe_json())
    assert db.record_file_probe(r"E:\Movies\Spirited Away.mkv", 900, info)
    assert _file_row(db, mid)["audio_languages"] == "ja,en"

    # the next plex scan deletes and rewrites the row: the languages come back
    db.upsert_movie("plex", item)
    row = _file_row(db, mid)
    assert (row["audio_languages"], row["subtitle_languages"]) == ("ja,en", "en")
    assert row["audio_channels"] == 6


def test_a_different_file_of_the_same_name_is_not_it(db):
    mid = db.upsert_movie("plex", {"server_id": "p1", "title": "Heat", "file": {
        "relative_path": "/m/Heat.mkv", "size_bytes": 500}})
    db.record_file_probe("/elsewhere/Heat.mkv", 501, mediainfo.parse_ffprobe(_probe_json()))
    assert _file_row(db, mid)["audio_languages"] is None


def test_jellyfin_streams_give_audio_and_embedded_subtitle_languages():
    from core.video.sources import _stream_langs
    streams = [{"Type": "Video"}, {"Type": "Audio", "Language": "jpn"},
               {"Type": "Audio", "Language": "eng"}, {"Type": "Audio"},
               {"Type": "Subtitle", "Language": "eng"},
               {"Type": "Subtitle", "Language": "spa", "IsExternal": True}]
    assert _stream_langs(streams, "Audio") == ["ja", "en"]
    # the .srt beside it isn't a track inside the file
    assert _stream_langs(streams, "Subtitle", embedded_only=True) == ["en"]


def test_naming_tokens_render_like_sonarr_and_vanish_when_empty():
    s = organization.normalize({"movie_template": "$title ($year) [$audiolangs][$sublangs]"})
    with_tags = organization.render_path("movie", "/m", {
        "title": "Spirited Away", "year": 2001,
        "audio_languages": ["ja", "en"], "subtitle_languages": "en"}, s, ".mkv")
    assert with_tags["filename"] == "Spirited Away (2001) [JA+EN][EN].mkv"
    none = organization.render_path("movie", "/m", {"title": "Heat", "year": 1995,
                                                     "audio_languages": ""}, s, ".mkv")
    assert none["filename"] == "Heat (1995).mkv"


def test_a_file_not_read_yet_is_never_renamed_on_a_guess(db, tmp_path, monkeypatch):
    # template wants [JA]: renaming an unread file would strip a tag it may have
    from api import video as videoapi
    from core.video import mass_rename
    movies = tmp_path / "Movies"
    (movies / "Spirited Away (2001)").mkdir(parents=True)
    real = movies / "Spirited Away (2001)" / "Spirited Away (2001) [JA].mkv"
    real.write_bytes(b"x" * 900)
    db.set_setting("movies_path", str(movies))
    organization.save(db, {"movie_template":
                           "$title ($year)/$title ($year) [$audiolangs]"})
    db.upsert_movie("plex", {"server_id": "p1", "title": "Spirited Away", "year": 2001,
                             "file": {"relative_path": str(real), "size_bytes": 900}})
    monkeypatch.setattr(videoapi, "_video_db", db)

    assert mass_rename.preview()["entries"] == []

    # once it's read, the name is checked like any other
    db.record_file_probe(str(real), 900, mediainfo.parse_ffprobe(_probe_json(audio=("jpn",))))
    assert mass_rename.preview()["entries"] == []        # [JA] is right
    db.record_file_probe(str(real), 900, mediainfo.parse_ffprobe(_probe_json(audio=("jpn", "eng"))))
    proposed = mass_rename.preview()["entries"]
    assert [os.path.basename(e["proposed"]) for e in proposed] == [
        "Spirited Away (2001) [JA+EN].mkv"]


def test_the_media_info_job_reads_files_once_and_skips_ones_it_cannot_see(db, tmp_path):
    from core.video.repair.base import JobContext
    from core.video.repair.media_info import MediaInfoJob
    movies = tmp_path / "Movies"
    movies.mkdir()
    here = movies / "Here.mkv"
    here.write_bytes(b"x" * 700)
    db.set_setting("movies_path", str(movies))
    a = db.upsert_movie("plex", {"server_id": "a", "title": "Here",
                                 "file": {"relative_path": str(here), "size_bytes": 700}})
    b = db.upsert_movie("plex", {"server_id": "b", "title": "Gone",
                                 "file": {"relative_path": "/nowhere/Gone.mkv", "size_bytes": 800}})
    probed = []

    def prober(path):
        probed.append(path)
        return mediainfo.parse_ffprobe(_probe_json(audio=("fre",), subs=()))

    ctx = JobContext(db=db, settings={"files_per_run": 300})
    result = MediaInfoJob().scan(ctx, prober=prober)
    assert (result.scanned, result.auto_fixed, result.skipped) == (2, 1, 1)
    assert _file_row(db, a)["audio_languages"] == "fr"
    assert _file_row(db, b)["audio_languages"] is None   # tried again next run

    # read once: the next run doesn't open it again
    probed.clear()
    MediaInfoJob().scan(ctx, prober=prober)
    assert probed == []


def test_an_existing_database_gains_the_columns(tmp_path):
    # the live db predates v60: the columns ride the migrations, not the CREATE
    import sqlite3
    path = tmp_path / "old.db"
    VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE media_files DROP COLUMN audio_languages")
    conn.execute("ALTER TABLE media_files DROP COLUMN subtitle_languages")
    conn.commit()
    conn.close()
    # a restart: init runs once per path per process
    from database import video_database
    video_database._initialized_paths.discard(str(path.resolve()))
    reopened = VideoDatabase(database_path=str(path))
    conn = reopened._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media_files)")}
    finally:
        conn.close()
    assert {"audio_languages", "subtitle_languages"} <= cols
