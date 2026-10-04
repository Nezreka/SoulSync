"""Tests for issue #1449 — artist.nfo with the MusicBrainz artist ID for
Jellyfin/Kodi/Emby.

Covers: the XML document shape (issue example), XML safety on hostile
names, atomic write, the never-overwrite-a-different-MBID guarantee, and
the import/backfill helper reading the file's own tags (so <name> exactly
matches ALBUMARTIST).
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import pytest
from mutagen.id3 import ID3, TPE2, TXXX


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


MBID = "b9ca7096-68de-455f-8a36-1f1ebb2abf2a"


# ---------------------------------------------------------------------------
# build_artist_nfo
# ---------------------------------------------------------------------------


class TestBuildArtistNfo:
    def test_issue_example_shape(self):
        from core.library.artist_nfo import build_artist_nfo
        xml = build_artist_nfo("Escape the Fate", MBID)
        root = ET.fromstring(xml)
        assert root.tag == "artist"
        assert root.findtext("name") == "Escape the Fate"
        assert root.findtext("musicbrainzartistid") == MBID

    def test_xml_declaration(self):
        from core.library.artist_nfo import build_artist_nfo
        xml = build_artist_nfo("Mammoth", "49a6efb9-9b52-44ce-8167-7cb1c21a8c45")
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_hostile_name_is_escaped(self):
        from core.library.artist_nfo import build_artist_nfo
        name = "Tom & Jerry <The> \"Band\""
        root = ET.fromstring(build_artist_nfo(name, MBID))
        assert root.findtext("name") == name  # round-trips through escaping

    def test_whitespace_trimmed(self):
        from core.library.artist_nfo import build_artist_nfo
        root = ET.fromstring(build_artist_nfo("  Mammoth  ", " " + MBID + " "))
        assert root.findtext("name") == "Mammoth"
        assert root.findtext("musicbrainzartistid") == MBID


# ---------------------------------------------------------------------------
# read_artist_nfo_mbid
# ---------------------------------------------------------------------------


class TestReadArtistNfoMbid:
    def test_round_trip(self, tmp_path):
        from core.library.artist_nfo import build_artist_nfo, read_artist_nfo_mbid
        nfo = tmp_path / "artist.nfo"
        nfo.write_text(build_artist_nfo("Mammoth", MBID), encoding="utf-8")
        assert read_artist_nfo_mbid(str(nfo)) == MBID

    def test_missing_file_returns_empty(self, tmp_path):
        from core.library.artist_nfo import read_artist_nfo_mbid
        assert read_artist_nfo_mbid(str(tmp_path / "nope.nfo")) == ""

    def test_garbage_file_returns_empty(self, tmp_path):
        from core.library.artist_nfo import read_artist_nfo_mbid
        nfo = tmp_path / "artist.nfo"
        nfo.write_text("not xml at all {{{", encoding="utf-8")
        assert read_artist_nfo_mbid(str(nfo)) == ""

    def test_nfo_without_mbid_returns_empty(self, tmp_path):
        from core.library.artist_nfo import read_artist_nfo_mbid
        nfo = tmp_path / "artist.nfo"
        nfo.write_text('<?xml version="1.0"?><artist><name>X</name></artist>', encoding="utf-8")
        assert read_artist_nfo_mbid(str(nfo)) == ""


# ---------------------------------------------------------------------------
# write_artist_nfo
# ---------------------------------------------------------------------------


class TestWriteArtistNfo:
    def test_writes_and_no_tmp_left(self, tmp_path):
        from core.library.artist_nfo import write_artist_nfo, read_artist_nfo_mbid
        ok, detail = write_artist_nfo(str(tmp_path), "Mammoth", MBID)
        assert ok
        assert detail.endswith("artist.nfo")
        assert not list(tmp_path.glob("*.tmp"))
        assert read_artist_nfo_mbid(detail) == MBID

    def test_existing_same_mbid_not_touched(self, tmp_path):
        from core.library.artist_nfo import write_artist_nfo
        target = tmp_path / "artist.nfo"
        target.write_text("hand-tuned content", encoding="utf-8")
        ok, detail = write_artist_nfo(str(tmp_path), "Mammoth", MBID)
        assert not ok and "already exists" in detail
        assert target.read_text(encoding="utf-8") == "hand-tuned content"

    def test_existing_different_mbid_never_overwritten(self, tmp_path):
        """Issue #1449: the user may have corrected the MBID by hand."""
        from core.library.artist_nfo import write_artist_nfo, build_artist_nfo
        target = tmp_path / "artist.nfo"
        target.write_text(build_artist_nfo("Mammoth", "aaaaaaaa-0000-0000-0000-000000000000"), encoding="utf-8")
        ok, detail = write_artist_nfo(str(tmp_path), "Mammoth", MBID)
        assert not ok and "different MBID" in detail
        root = ET.parse(str(target)).getroot()
        assert root.findtext("musicbrainzartistid") == "aaaaaaaa-0000-0000-0000-000000000000"

    def test_explicit_overwrite_replaces(self, tmp_path):
        from core.library.artist_nfo import write_artist_nfo, read_artist_nfo_mbid
        target = tmp_path / "artist.nfo"
        target.write_text("old", encoding="utf-8")
        ok, detail = write_artist_nfo(str(tmp_path), "Mammoth", MBID, overwrite=True)
        assert ok
        assert read_artist_nfo_mbid(detail) == MBID

    def test_missing_inputs_rejected(self, tmp_path):
        from core.library.artist_nfo import write_artist_nfo
        assert write_artist_nfo(str(tmp_path / "nope"), "X", MBID)[0] is False
        assert write_artist_nfo(str(tmp_path), "", MBID)[0] is False
        assert write_artist_nfo(str(tmp_path), "X", "")[0] is False
        assert not (tmp_path / "artist.nfo").exists()


# ---------------------------------------------------------------------------
# ensure_artist_nfo_for_track
# ---------------------------------------------------------------------------


class TestEnsureArtistNfoForTrack:
    def _track(self, tmp_path, name="Mammoth", mbid=MBID):
        """A plausible artist/album/track layout. Tag reading is stubbed via
        monkeypatch (mutagen can't open tag-only files); one real end-to-end
        test below proves the actual tag mapping."""
        artist = tmp_path / "Mammoth"
        album = artist / "Mammoth - Mammoth"
        album.mkdir(parents=True)
        track = album / "01 - Track.mp3"
        track.write_bytes(b"fake-audio")
        tags = {"available": True, "tags": {}}
        if name is not None:
            tags["tags"]["album_artist"] = name
        if mbid is not None:
            tags["tags"]["musicbrainz_albumartistid"] = mbid
        return str(track), str(artist), tags

    def _stub_tags(self, monkeypatch, tags):
        import core.library.file_tags as file_tags
        monkeypatch.setattr(file_tags, "read_embedded_tags", lambda p: tags)

    def test_writes_nfo_from_file_tags(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(track, Config(**{"library.write_artist_nfo": True}))
        assert ok, detail
        nfo = os.path.join(artist, "artist.nfo")
        root = ET.parse(nfo).getroot()
        # <name> exactly matches the ALBUMARTIST tag
        assert root.findtext("name") == "Mammoth"
        assert root.findtext("musicbrainzartistid") == MBID

    def test_setting_off_skips(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(track, Config())
        assert not ok and detail == "setting disabled"
        assert not os.path.exists(os.path.join(artist, "artist.nfo"))

    def test_force_bypasses_setting(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(track, Config(), force=True)
        assert ok, detail
        assert os.path.exists(os.path.join(artist, "artist.nfo"))

    def test_no_mbid_in_tags_skips(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path, mbid=None)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(track, Config(**{"library.write_artist_nfo": True}))
        assert not ok and "MusicBrainz" in detail
        assert not os.path.exists(os.path.join(artist, "artist.nfo"))

    def test_no_albumartist_in_tags_skips(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path, name=None)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(track, Config(**{"library.write_artist_nfo": True}))
        assert not ok and "ALBUMARTIST" in detail

    def test_idempotent_second_call(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        cfg = Config(**{"library.write_artist_nfo": True})
        track, artist, tags = self._track(tmp_path)
        self._stub_tags(monkeypatch, tags)
        assert ensure_artist_nfo_for_track(track, cfg)[0]
        ok, detail = ensure_artist_nfo_for_track(track, cfg)
        assert not ok and "already exists" in detail

    def test_never_raises_on_bad_input(self):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        ok, detail = ensure_artist_nfo_for_track(None, None)
        assert not ok
        ok, detail = ensure_artist_nfo_for_track("/nonexistent/x.mp3", Config(**{"library.write_artist_nfo": True}))
        assert not ok

    def test_skips_when_derived_folder_is_library_root(self, tmp_path, monkeypatch):
        """Flat layout: track directly in the artist folder means the derived
        folder IS the library root — never plant artist.nfo there."""
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        artist = tmp_path / "Mammoth"
        artist.mkdir()
        track = artist / "track.mp3"
        track.write_bytes(b"fake-audio")
        tags = {"available": True, "tags": {
            "album_artist": "Mammoth", "musicbrainz_albumartistid": MBID}}
        self._stub_tags(monkeypatch, tags)
        cfg = Config(**{
            "library.write_artist_nfo": True,
            "library.music_paths": [str(tmp_path)],
        })
        ok, detail = ensure_artist_nfo_for_track(str(track), cfg)
        assert not ok and "library root" in detail
        assert not (tmp_path / "artist.nfo").exists()

    def test_fallback_mbid_used_when_tags_lack_it(self, tmp_path, monkeypatch):
        """Backfill: files predating MB tag embedding get the DB-known MBID."""
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path, mbid=None)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(
            track, Config(**{"library.write_artist_nfo": True}),
            fallback_mbid=MBID, fallback_name="Mammoth")
        assert ok, detail
        root = ET.parse(os.path.join(artist, "artist.nfo")).getroot()
        assert root.findtext("musicbrainzartistid") == MBID

    def test_fallback_mbid_ignored_on_name_mismatch(self, tmp_path, monkeypatch):
        """A re-identified artist's new MBID must never pair with a stale
        file-tag name."""
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path, mbid=None)
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(
            track, Config(**{"library.write_artist_nfo": True}),
            fallback_mbid=MBID, fallback_name="Mammoth WVH")
        assert not ok and "MusicBrainz" in detail
        assert not os.path.exists(os.path.join(artist, "artist.nfo"))

    def test_file_tag_mbid_beats_fallback(self, tmp_path, monkeypatch):
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, tags = self._track(tmp_path, mbid="file-mbid-wins")
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(
            track, Config(**{"library.write_artist_nfo": True}),
            fallback_mbid=MBID, fallback_name="Mammoth")
        assert ok, detail
        root = ET.parse(os.path.join(artist, "artist.nfo")).getroot()
        assert root.findtext("musicbrainzartistid") == "file-mbid-wins"

    def test_mp4_mbid_key_shape(self, tmp_path, monkeypatch):
        """MP4 freeform atoms surface as musicbrainz_album_artist_id."""
        from core.library.artist_nfo import ensure_artist_nfo_for_track
        track, artist, _ = self._track(tmp_path, mbid=None)
        tags = {"available": True, "tags": {
            "album_artist": "Mammoth", "musicbrainz_album_artist_id": MBID}}
        self._stub_tags(monkeypatch, tags)
        ok, detail = ensure_artist_nfo_for_track(
            track, Config(**{"library.write_artist_nfo": True}))
        assert ok, detail
        root = ET.parse(os.path.join(artist, "artist.nfo")).getroot()
        assert root.findtext("musicbrainzartistid") == MBID

    @pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="needs ffmpeg")
    def test_real_mp3_tags_end_to_end(self, tmp_path):
        """Proves the actual mutagen tag mapping: TPE2 -> album_artist and
        TXXX 'MusicBrainz Album Artist Id' -> musicbrainz_albumartistid."""
        import shutil
        import subprocess
        from core.library.artist_nfo import ensure_artist_nfo_for_track

        artist = tmp_path / "Mammoth"
        album = artist / "Mammoth - Mammoth"
        album.mkdir(parents=True)
        track = album / "01 - Track.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", "0.2", "-q:a", "9", str(track)],
            check=True)
        tags = ID3(str(track))
        tags.add(TPE2(encoding=3, text=["Mammoth"]))
        tags.add(TXXX(encoding=3, desc="MusicBrainz Album Artist Id", text=[MBID]))
        tags.save()

        ok, detail = ensure_artist_nfo_for_track(
            str(track), Config(**{"library.write_artist_nfo": True}))
        assert ok, detail
        root = ET.parse(os.path.join(str(artist), "artist.nfo")).getroot()
        assert root.findtext("name") == "Mammoth"
        assert root.findtext("musicbrainzartistid") == MBID


# ---------------------------------------------------------------------------
# backfill repair job
# ---------------------------------------------------------------------------


class TestArtistNfoBackfillJob:
    def _seed_db(self, tmp_path):
        from database.music_database import MusicDatabase
        db = MusicDatabase(str(tmp_path / "music.db"))
        artist = tmp_path / "Mammoth"
        album = artist / "Mammoth - Mammoth"
        album.mkdir(parents=True)
        track = album / "01 - Track.mp3"
        track.write_bytes(b"fake-audio")  # tag reads are stubbed below
        with db._get_connection() as conn:
            conn.execute("INSERT INTO artists (id, name, musicbrainz_id) VALUES (1, 'Mammoth', ?)", (MBID,))
            conn.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Mammoth', 1)")
            conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id) VALUES (1, 'T', ?, 1, 1)", (str(track),))
            # artist without MBID — must be skipped, not crash
            conn.execute("INSERT INTO artists (id, name) VALUES (2, 'No MBID')")
            conn.commit()
        return db, str(artist)

    def _stub_tags(self, monkeypatch):
        import core.library.file_tags as file_tags
        monkeypatch.setattr(
            file_tags, "read_embedded_tags",
            lambda p: {"available": True, "tags": {
                "album_artist": "Mammoth",
                "musicbrainz_albumartistid": MBID,
            }})

    def _context(self, db, tmp_path):
        from types import SimpleNamespace
        from core.repair_jobs.base import JobContext
        return JobContext(
            db=db,
            transfer_folder=str(tmp_path),
            config_manager=Config(),  # setting OFF — job is its own opt-in
            create_finding=None,
        )

    def test_backfill_writes_missing_nfo(self, tmp_path, monkeypatch):
        from core.repair_jobs.artist_nfo_backfill import ArtistNfoBackfillJob
        db, artist = self._seed_db(tmp_path)
        self._stub_tags(monkeypatch)
        job = ArtistNfoBackfillJob()
        result = job.scan(self._context(db, tmp_path))
        assert result.auto_fixed == 1
        assert result.errors == 0
        nfo = os.path.join(artist, "artist.nfo")
        assert os.path.exists(nfo)
        root = ET.parse(nfo).getroot()
        assert root.findtext("name") == "Mammoth"
        assert root.findtext("musicbrainzartistid") == MBID

    def test_backfill_second_run_is_steady_state(self, tmp_path, monkeypatch):
        from core.repair_jobs.artist_nfo_backfill import ArtistNfoBackfillJob
        db, artist = self._seed_db(tmp_path)
        self._stub_tags(monkeypatch)
        job = ArtistNfoBackfillJob()
        ctx = self._context(db, tmp_path)
        assert job.scan(ctx).auto_fixed == 1
        second = job.scan(ctx)
        assert second.auto_fixed == 0 and second.errors == 0

    def test_backfill_respects_hand_fixed_mbid(self, tmp_path, monkeypatch):
        from core.repair_jobs.artist_nfo_backfill import ArtistNfoBackfillJob
        from core.library.artist_nfo import build_artist_nfo
        db, artist = self._seed_db(tmp_path)
        self._stub_tags(monkeypatch)
        nfo = os.path.join(artist, "artist.nfo")
        with open(nfo, "w", encoding="utf-8") as f:
            f.write(build_artist_nfo("Mammoth", "hand-fixed-mbid"))
        job = ArtistNfoBackfillJob()
        result = job.scan(self._context(db, tmp_path))
        assert result.auto_fixed == 0
        assert ET.parse(nfo).getroot().findtext("musicbrainzartistid") == "hand-fixed-mbid"

    def test_candidates_only_artists_with_mbid(self, tmp_path, monkeypatch):
        from core.repair_jobs.artist_nfo_backfill import ArtistNfoBackfillJob
        db, _ = self._seed_db(tmp_path)
        self._stub_tags(monkeypatch)
        rows = ArtistNfoBackfillJob()._candidates(self._context(db, tmp_path))
        assert len(rows) == 1
        assert rows[0][1] == "Mammoth" and rows[0][2] == MBID

    def test_backfill_uses_db_mbid_when_tags_lack_it(self, tmp_path, monkeypatch):
        """Files predating MB tag embedding: the DB row's musicbrainz_id
        backfills them (this is the backfill's core audience)."""
        from core.repair_jobs.artist_nfo_backfill import ArtistNfoBackfillJob
        db, artist = self._seed_db(tmp_path)
        # tags carry a name but no MBID at all
        import core.library.file_tags as file_tags
        monkeypatch.setattr(
            file_tags, "read_embedded_tags",
            lambda p: {"available": True, "tags": {"album_artist": "Mammoth"}})
        job = ArtistNfoBackfillJob()
        result = job.scan(self._context(db, tmp_path))
        assert result.auto_fixed == 1
        nfo = os.path.join(artist, "artist.nfo")
        root = ET.parse(nfo).getroot()
        assert root.findtext("name") == "Mammoth"  # name still from file tags
        assert root.findtext("musicbrainzartistid") == MBID  # MBID from DB

    def test_job_registers(self):
        from core.repair_jobs import get_all_jobs
        job = get_all_jobs()["artist_nfo_backfill"]
        assert job.writes_library_files is True
        assert job.auto_fix is True
        assert job.default_enabled is False
