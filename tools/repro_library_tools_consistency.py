"""Reproduce the 2026-10-05 metadata consistency investigation, offline.

Run from the checkout: python tools/repro_library_tools_consistency.py
These are observations of current behaviour, not desired-behaviour regression
tests. A later fix may intentionally make an observation fail. Only temporary
databases and synthetic metadata containers are used. No production inputs,
credentials, external requests, downloads or background app workers are needed.
Requires the project's web/metadata dependencies, not its DSP/model packages.
"""

from __future__ import annotations

import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class Database:
    def __init__(self, path):
        self.database_path = str(path)

    def _get_connection(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


class Observations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="soulsync-consistency-case-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "07 - Track 7.flac"
        # Same minimal FLAC metadata container as the existing preservation
        # tests. It has no audio frames: these probes assess TAGS, not audio.
        self.file.write_bytes(
            b"fLaC" + b"\x80\x00\x00\x22" + b"\x00\x10\x00\x10"
            + b"\x00" * 6 + b"\x0a\xc4\x42\xf0\x00\x00\x00\x00" + b"\x00" * 16
        )
        from mutagen.flac import FLAC, Picture
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(buf, format="PNG")
        self.image = buf.getvalue()
        audio = FLAC(self.file)
        for key, value in {
            "title": "Track 7", "artist": "Artist", "albumartist": "Artist",
            "album": "Album", "tracknumber": "7", "discnumber": "1",
            "date": "2026", "genre": "Rock",
        }.items():
            audio[key] = [value]
        picture = Picture()
        picture.type, picture.mime, picture.data = 3, "image/png", self.image
        audio.add_picture(picture)
        audio.save()
        (self.root / "cover.png").write_bytes(self.image)

        from core.library2.schema import ensure_library_v2_schema

        self.db = Database(self.root / "library.db")
        with closing(self.db._get_connection()) as conn, conn:
            ensure_library_v2_schema(conn)
            conn.execute("INSERT INTO lib2_artists(id,name,sort_name) VALUES(1,'Artist','Artist')")
            conn.execute("INSERT INTO lib2_albums(id,primary_artist_id,title,image_url) VALUES(1,1,'Album','')")
            conn.execute("INSERT INTO lib2_tracks(id,album_id,title,track_number,disc_number) VALUES(1,1,'Track 7',7,1)")
            conn.execute("INSERT INTO lib2_track_files(id,track_id,path,format,is_primary) VALUES(1,1,?,'flac',1)", (str(self.file),))
            conn.execute("CREATE TABLE repair_findings(id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, status TEXT, file_path TEXT, user_action TEXT, resolved_at TEXT, updated_at TEXT)")
        self.cfg = Config()
        self.tracklist = [
            {"name": f"Track {n}", "track_number": n, "disc_number": 1}
            for n in range(1, 14)
        ]

    def cover_scan(self):
        from core.library2.provider_adapters import ArtworkProviderResult
        from core.repair_jobs.missing_cover_art import MissingCoverArtJob

        findings = []
        context = SimpleNamespace(
            db=self.db, config_manager=self.cfg, check_stop=lambda: False,
            wait_if_paused=lambda: False, update_progress=None,
            create_finding=lambda **kw: findings.append(kw) or True,
        )
        art = ArtworkProviderResult(kind="album", source="spotify", provider_entity_id="fixture", url="https://example.test/cover.png")
        with patch("core.library2.provider_adapters.fetch_artwork_url", return_value=art):
            MissingCoverArtJob().scan(context)
        return findings

    def rescan(self):
        from core.library2.scan import rescan_files

        with patch("core.library2.paths.resolve_lib2_path", side_effect=lambda p, **kw: p if Path(p).is_file() else None):
            rescan_files(self.db, album_ids=[1], manual=True)
        with closing(self.db._get_connection()) as conn, conn:
            return dict(conn.execute("SELECT * FROM lib2_track_files WHERE id=1").fetchone())

    def test_embedded_cover_and_sidecar_but_blank_db_image(self):
        from core.library2.artwork import build_artwork

        self.assertEqual(json.loads(self.rescan()["metadata_gaps_json"]), [])
        with closing(self.db._get_connection()) as conn, \
             patch("core.library2.artwork._provider_art_url", side_effect=AssertionError("Unexpected provider lookup")):
            cached_cover = build_artwork(self.db, conn, self.cfg, "album", 1)
        self.assertTrue(cached_cover and Path(cached_cover).is_file())
        findings = self.cover_scan()
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["details"]["db_missing"])
        self.assertFalse(findings[0]["details"]["embed_missing"])

    def test_embedding_disabled_still_reported_missing(self):
        from mutagen.flac import FLAC

        audio = FLAC(self.file)
        audio.clear_pictures()
        audio.save()
        self.cfg = Config(**{"metadata_enhancement.embed_album_art": False})
        with closing(self.db._get_connection()) as conn, conn:
            conn.execute("UPDATE lib2_albums SET image_url='https://example.test/cover.png'")
        finding = self.cover_scan()[0]
        self.assertTrue(finding["details"]["embed_missing"])
        self.assertFalse(finding["details"]["db_missing"])

    def test_number_present_is_gap_free_even_when_wrong(self):
        from mutagen.flac import FLAC
        from core.repair_jobs.track_number_repair import _check_single_track

        audio = FLAC(self.file)
        audio["tracknumber"] = ["3"]
        audio.save()
        row = self.rescan()
        self.assertEqual(json.loads(row["metadata_gaps_json"]), [])
        finding = _check_single_track(str(self.file), self.file.name, self.tracklist, .80)
        self.assertEqual(finding["details"]["current_track_num"], 3)
        self.assertEqual(finding["details"]["correct_track_num"], 7)

    def test_missing_album_total_defaults_to_one_then_flags_correct_position(self):
        from core.metadata import source
        from core.metadata.track_number_format import format_track_number_tag
        from core.repair_jobs import track_number_repair as tnr
        from mutagen.id3 import ID3, TIT2, TRCK

        context = {"source": "spotify", "album": {"name": "Album"},
                   "original_search_result": {"title": "Track 7", "artist": "Artist"}}
        with patch.object(source, "get_config_manager", return_value=self.cfg):
            metadata = source.extract_source_metadata(
                context, {"name": "Artist"}, {"is_album": True, "album_name": "Album", "track_number": 7},
            )
        self.assertEqual(metadata["total_tracks"], 1)
        tag = format_track_number_tag(metadata["track_number"], metadata["total_tracks"])
        self.assertEqual(tag, "7/1")
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Track 7"]))
        tags.add(TRCK(encoding=3, text=[tag]))
        # Use real Mutagen ID3 frames; inject the container at the I/O boundary.
        with patch("mutagen.File", return_value=SimpleNamespace(tags=tags)):
            finding = tnr._check_single_track("07 - Track 7.mp3", "07 - Track 7.mp3", self.tracklist, .80)
        self.assertEqual(finding["details"]["current_track_num"], 7)
        self.assertEqual(finding["details"]["correct_track_num"], 7)
        self.assertIn("Total tracks: 1 -> 13", finding["details"]["changes"])
        # The new Vorbis writer stores a separate TRACKTOTAL. Unlike ID3, the
        # repair reader never reads that field, so the same wrong total in
        # FLAC passes this check. This is a format-dependent inconsistency.
        from mutagen.flac import FLAC
        audio = FLAC(self.file)
        audio["tracktotal"] = ["1"]
        audio.save()
        self.assertIsNone(tnr._check_single_track(str(self.file), self.file.name, self.tracklist, .80))

    def test_single_edition_still_uses_group_numbering(self):
        from core.repair_jobs.track_number_repair import _api_tracks_for_subject

        edition = [{"name": "Track 7", "track_number": 7, "lib2_track_id": 1}]
        group = [{"name": "Track 7", "track_number": 3, "lib2_track_id": 1}]
        selected = _api_tracks_for_subject({"track_id": 1}, group, {10: edition}, {1: [10]})
        self.assertEqual(selected[0]["track_number"], 3)

    def test_aborted_tag_saves_still_return_enrichment_success(self):
        from core.metadata import enrichment
        from mutagen.flac import FLAC

        cfg = Config(**{"metadata_enhancement.embed_album_art": False})
        metadata = {"title": "New Title", "artist": "Artist", "album": "Album", "album_artist": "Artist", "track_number": 7, "total_tracks": 13}
        with patch.object(enrichment, "get_config_manager", return_value=cfg), \
             patch.object(enrichment, "extract_source_metadata", return_value=metadata), \
             patch.object(enrichment, "embed_source_ids"), \
             patch.object(enrichment, "save_audio_file", return_value=False) as save:
            result = enrichment.enhance_file_metadata(str(self.file), {}, {"name": "Artist"}, {})
        self.assertGreaterEqual(save.call_count, 2)
        self.assertTrue(result)
        self.assertEqual(FLAC(self.file)["title"], ["Track 7"])

    def test_external_correction_leaves_pending_finding_after_rescan_and_sweep(self):
        from core.repair_worker import RepairWorker

        with closing(self.db._get_connection()) as conn, conn:
            conn.execute("INSERT INTO repair_findings(id,job_id,finding_type,status,file_path) VALUES(1,'track_number_repair','track_number_mismatch','pending',?)", (str(self.file),))
        self.rescan()  # file is now correctly numbered 7
        worker = RepairWorker.__new__(RepairWorker)
        worker.db, worker.transfer_folder, worker._config_manager = self.db, str(self.root), self.cfg
        self.assertEqual(worker.retire_vanished_findings("track_number_repair"), 0)
        with closing(self.db._get_connection()) as conn, conn:
            self.assertEqual(conn.execute("SELECT status FROM repair_findings WHERE id=1").fetchone()[0], "pending")


if __name__ == "__main__":
    # Set isolation BEFORE importing any app module. Never read real config or
    # databases; even unexpected singleton construction stays in this directory.
    with tempfile.TemporaryDirectory(prefix="soulsync-consistency-env-") as isolated:
        for name, filename in {
            "DATABASE_PATH": "music.db", "VIDEO_DATABASE_PATH": "video.db",
            "AUDIOBOOK_DATABASE_PATH": "audiobooks.db", "SOULSYNC_CONFIG_PATH": "config.json",
            "SOULSYNC_IMAGE_CACHE_DIR": "image-cache",
        }.items():
            os.environ[name] = str(Path(isolated) / filename)
        try:
            run = unittest.main(verbosity=2, exit=False)
        finally:
            # Module-level settings/metadata imports can construct the isolated
            # MusicDatabase singleton. Close it before Windows removes the temp
            # directory, where open SQLite handles otherwise prevent cleanup.
            from database.music_database import close_database
            close_database()
    sys.exit(0 if run.result.wasSuccessful() else 1)
