"""cross-format duplicates + lossy-companion guard (kvkarlsson, aug 25).

same folder held '01 A Butterfly....flac' AND '.ogg' - obvious dupes the
detector never found because the filename pass keyed on (stem, EXTENSION),
which is structurally blind to 'same track, two formats'. the fix drops the
extension from the key and adds one guard: a lossless file with its own
intentional lossy copy beside it (the lossy-copy feature) is not a dupe.
"""

from collections import defaultdict
from types import SimpleNamespace

from core.repair_jobs.duplicate_detector import (
    DuplicateDetectorJob,
    _is_lossy_companion_pair,
    _normalize,
)
from core.library.duplicate_rules import is_lossy_companion_file


def _track(track_id, *, title, artist="Double Duo", album="Crossword Puzzle",
           file_path="", bitrate=900, duration=180.0):
    return {
        'id': track_id,
        'title': title,
        'norm_title': _normalize(title),
        'artist': artist,
        'norm_artist': _normalize(artist),
        'album': album,
        'file_path': file_path,
        'bitrate': bitrate,
        'duration': duration,
        'album_thumb_url': None,
        'artist_thumb_url': None,
        'artist_id': None,
    }


class _Ctx:
    def __init__(self):
        self.findings = []
        self.create_finding = lambda **kw: self.findings.append(kw) or True
        self.report_progress = lambda **kw: None
        self.update_progress = lambda *a, **kw: None
        self.check_stop = lambda: False


def _scan(job, bucket, ctx, companions=frozenset()):
    result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
    job._scan_bucket(
        bucket_tracks=bucket,
        require_metadata_match=False,
        title_threshold=0.85,
        artist_threshold=0.80,
        ignore_cross_album=False,
        found_groups=set(),
        processed_holder={'count': 0},
        total=len(bucket),
        result=result,
        context=ctx,
        lossy_companion_exts=companions,
    )
    return result


class TestCrossFormatDetection:
    def test_flac_plus_ogg_same_folder_is_one_group(self):
        """the reported case, end to end through both stages."""
        job = DuplicateDetectorJob()
        ctx = _Ctx()
        folder = "//OPENMEDIAVAULT/music/Double Duo/Crossword Puzzle"
        flac = _track(1, title="A Butterfly, Bee, Mantis, And Grasshopper",
                      file_path=f"{folder}/01 A Butterfly, Bee, Mantis, And Grasshopper.flac")
        # the ogg's tag title came out different, so the title pass
        # bucketed them apart - the exact reported shape
        ogg = _track(2, title="Track 01",
                     file_path=f"{folder}/01 A Butterfly, Bee, Mantis, And Grasshopper.ogg",
                     bitrate=256)
        fname_buckets = job._build_filename_buckets(
            buckets=defaultdict(list, _=[flac, ogg]), found_groups=set())
        assert len(fname_buckets) == 1
        result = _scan(job, next(iter(fname_buckets.values())), ctx)
        assert result.findings_created == 1
        assert ctx.findings[0]['details']['count'] == 2

    def test_different_durations_still_not_grouped(self):
        """same stem but genuinely different audio stays apart."""
        job = DuplicateDetectorJob()
        ctx = _Ctx()
        a = _track(1, title="Yellow", file_path="/lib/a/Yellow.flac", duration=120.0)
        b = _track(2, title="Yellow", file_path="/lib/b/Yellow.ogg", duration=260.0)
        result = _scan(job, [a, b], ctx)
        assert result.findings_created == 0


class TestLossyCompanionGuard:
    def test_companion_pair_is_skipped_when_feature_writes_that_ext(self):
        job = DuplicateDetectorJob()
        ctx = _Ctx()
        flac = _track(1, title="Song", file_path="/lib/Artist/Album/Song.flac")
        mp3 = _track(2, title="Song", file_path="/lib/Artist/Album/Song.mp3", bitrate=320)
        result = _scan(job, [flac, mp3], ctx, companions=frozenset({'.mp3'}))
        assert result.findings_created == 0

    def test_same_pair_is_flagged_when_the_feature_is_off(self):
        """empty companion set = nobody uses lossy copies = real duplicate."""
        job = DuplicateDetectorJob()
        ctx = _Ctx()
        flac = _track(1, title="Song", file_path="/lib/Artist/Album/Song.flac")
        mp3 = _track(2, title="Song", file_path="/lib/Artist/Album/Song.mp3", bitrate=320)
        result = _scan(job, [flac, mp3], ctx, companions=frozenset())
        assert result.findings_created == 1

    def test_ogg_is_never_a_companion(self):
        """the feature writes mp3/opus/m4a only - a stray ogg next to a
        flac is a dupe even for lossy-copy users."""
        job = DuplicateDetectorJob()
        ctx = _Ctx()
        flac = _track(1, title="Song", file_path="/lib/Artist/Album/Song.flac")
        ogg = _track(2, title="Song", file_path="/lib/Artist/Album/Song.ogg", bitrate=256)
        result = _scan(job, [flac, ogg], ctx, companions=frozenset({'.mp3'}))
        assert result.findings_created == 1


class TestIsLossyCompanionPair:
    def test_requires_same_folder(self):
        assert not _is_lossy_companion_pair(
            "/a/Song.flac", "/b/Song.mp3", frozenset({'.mp3'}))

    def test_requires_same_stem(self):
        assert not _is_lossy_companion_pair(
            "/a/Song.flac", "/a/Other.mp3", frozenset({'.mp3'}))

    def test_two_lossy_files_are_not_a_companion_pair(self):
        assert not _is_lossy_companion_pair(
            "/a/Song.mp3", "/a/Song.opus", frozenset({'.mp3', '.opus'}))

    def test_windows_paths_and_case(self):
        assert _is_lossy_companion_pair(
            "C:\\Music\\Album\\Song.FLAC", "C:\\music\\album\\Song.Mp3",
            frozenset({'.mp3'}))

    def test_alac_m4a_is_protected_with_mp3_or_opus(self, monkeypatch):
        monkeypatch.setattr(
            "core.imports.file_ops.m4a_codec",
            lambda path: "alac" if str(path).lower().endswith(".m4a") else None,
        )

        for lossy_ext in (".mp3", ".opus"):
            assert _is_lossy_companion_pair(
                "/music/Album/Song.m4a",
                f"/music/Album/Song{lossy_ext}",
                frozenset({lossy_ext}),
            )

    def test_aac_m4a_is_not_treated_as_lossless_source(self, monkeypatch):
        monkeypatch.setattr("core.imports.file_ops.m4a_codec", lambda _path: "aac")

        assert not _is_lossy_companion_pair(
            "/music/Album/Song.m4a",
            "/music/Album/Song.opus",
            frozenset({".opus"}),
        )

    def test_empty_set_short_circuits(self):
        assert not _is_lossy_companion_pair(
            "/a/Song.flac", "/a/Song.mp3", frozenset())

    def test_filesystem_companion_fallback_requires_lossless_sibling(self, tmp_path):
        flac = tmp_path / "Song.flac"
        mp3 = tmp_path / "Song.mp3"
        flac.write_bytes(b"lossless")
        mp3.write_bytes(b"lossy")

        assert is_lossy_companion_file(mp3, frozenset({".mp3"})) is True
        flac.unlink()
        assert is_lossy_companion_file(mp3, frozenset({".mp3"})) is False


class TestCompanionExtsResolution:
    def test_reads_global_toggle_and_profiles(self, tmp_path):
        import sqlite3

        dbfile = tmp_path / "q.db"
        conn = sqlite3.connect(dbfile)
        conn.execute("CREATE TABLE quality_profiles ("
                     "lossy_copy_enabled INTEGER, lossy_copy_codec TEXT)")
        conn.execute("INSERT INTO quality_profiles VALUES (1, 'opus')")
        conn.execute("INSERT INTO quality_profiles VALUES (0, 'aac')")
        conn.commit()
        conn.close()

        class Db:
            def _get_connection(self):
                return sqlite3.connect(dbfile)

        class Cfg:
            def get(self, key, default=None):
                return {'lossy_copy.enabled': True,
                        'lossy_copy.codec': 'mp3'}.get(key, default)

        ctx = SimpleNamespace(config_manager=Cfg(), db=Db())
        exts = DuplicateDetectorJob()._lossy_companion_exts(ctx)
        # global mp3 + the ENABLED profile's opus; the disabled profile's
        # aac stays out
        assert exts == {'.mp3', '.opus'}

    def test_missing_table_and_config_mean_empty(self):
        import sqlite3

        class Db:
            def _get_connection(self):
                return sqlite3.connect(':memory:')

        ctx = SimpleNamespace(config_manager=None, db=Db())
        assert DuplicateDetectorJob()._lossy_companion_exts(ctx) == set()
