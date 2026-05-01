"""Regression tests for duplicate-detector slskd dedup-suffix bucket pass.

Discord-reported (Mushy): the watchlist re-downloaded the same OST track
seven times — every retry landed in the same album folder with a
slskd dedup tail (``Track_<19-digit-timestamp>.mp3``) appended by slskd
to avoid clobbering the prior copy. The library scan then ingested all
seven files. The duplicate detector found only one of them because the
title-prefix bucket compared on tag titles, which the media-server scan
sometimes parses inconsistently (or leaves blank) for files written by
slskd directly into a library folder. So the seven copies got bucketed
apart by their parsed titles and never compared.

The fix adds a second pass: re-bucket the leftover tracks (the ones the
title pass didn't already group) by canonical filename stem with the
slskd dedup tail stripped. Files that share a canonical filename + same
extension are grouped without re-checking title/artist similarity —
filename agreement is itself strong evidence the files came from the
same source download.
"""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from core.repair_jobs.duplicate_detector import DuplicateDetectorJob


def _make_track(
    track_id,
    *,
    title,
    artist="John Swihart",
    album="Various Artists - Napoleon Dynamite OST",
    file_path="",
    bitrate=320,
    duration=180.0,
):
    """Build the dict shape that `_scan_bucket` and `_build_filename_buckets` expect."""
    from core.repair_jobs.duplicate_detector import _normalize

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
    }


class _FakeContext:
    """Minimal JobContext stand-in for testing scan logic."""

    def __init__(self):
        self.findings = []
        self.create_finding = self._create_finding
        self.report_progress = lambda **kw: None
        self.update_progress = lambda *a, **kw: None
        self.check_stop = lambda: False

    def _create_finding(self, **kwargs):
        self.findings.append(kwargs)


# ---------------------------------------------------------------------------
# _build_filename_buckets — strips slskd dedup suffix and groups leftovers
# ---------------------------------------------------------------------------


class TestBuildFilenameBuckets:
    def setup_method(self):
        self.job = DuplicateDetectorJob()

    def test_strips_dedup_suffix_and_groups_orphans(self):
        """The exact scenario: one canonical file plus six slskd dedup
        siblings. All seven must collapse into one bucket."""
        base_dir = "/data/torrents/music/Various Artists - Napoleon Dynamite OST"
        canonical = _make_track(
            1, title="Heres Rico Musiq",
            file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq.mp3",
        )
        siblings = [
            _make_track(
                i + 2,
                title=f"Heres Rico Musiq Variant {i}",  # different parsed title
                file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq_{ts}.mp3",
            )
            for i, ts in enumerate([
                "639122324339578022", "639126674226945470", "639127689938113502",
                "639130174269948395", "639132075826102610", "639132543802519474",
            ])
        ]
        all_tracks = [canonical, *siblings]
        buckets = defaultdict(list)
        # All in one synthetic title bucket — irrelevant, _build_filename_buckets
        # ignores the title bucket key and only looks at file_path.
        buckets['heres'] = all_tracks

        fname_buckets = self.job._build_filename_buckets(buckets=buckets, found_groups=set())
        assert len(fname_buckets) == 1
        bucket = next(iter(fname_buckets.values()))
        assert {t['id'] for t in bucket} == {1, 2, 3, 4, 5, 6, 7}

    def test_skips_tracks_already_in_a_group(self):
        """`found_groups` carries IDs from the title-bucket pass — those
        must not be re-considered."""
        canonical = _make_track(1, title="Song", file_path="/lib/Song.mp3")
        sibling = _make_track(2, title="Song", file_path="/lib/Song_639122324339578022.mp3")
        buckets = defaultdict(list, song=[canonical, sibling])
        fname_buckets = self.job._build_filename_buckets(buckets=buckets, found_groups={1})
        assert fname_buckets == {}  # singleton bucket dropped

    def test_drops_singleton_buckets(self):
        """A canonical filename with no siblings carries no comparison
        value — keeping it just inflates the inner loop."""
        track = _make_track(1, title="Song", file_path="/lib/Song.mp3")
        buckets = defaultdict(list, song=[track])
        assert self.job._build_filename_buckets(buckets=buckets, found_groups=set()) == {}

    def test_different_extensions_bucket_separately(self):
        """A .mp3 next to a .flac with the same canonical stem are
        different files (different formats), not slskd dedup orphans."""
        mp3 = _make_track(1, title="Song", file_path="/lib/Song.mp3")
        flac = _make_track(2, title="Song", file_path="/lib/Song_639122324339578022.flac")
        buckets = defaultdict(list, song=[mp3, flac])
        assert self.job._build_filename_buckets(buckets=buckets, found_groups=set()) == {}

    def test_skips_tracks_without_file_path(self):
        """Defensive: DB rows can carry NULL file_path — skip them."""
        with_path = _make_track(1, title="Song", file_path="/lib/Song.mp3")
        no_path = _make_track(2, title="Song", file_path=None)
        buckets = defaultdict(list, song=[with_path, no_path])
        assert self.job._build_filename_buckets(buckets=buckets, found_groups=set()) == {}

    def test_windows_paths_handled(self):
        """Backslash separators (Windows) must canonicalize the same as
        forward-slash (Linux) so the bucket key is consistent."""
        a = _make_track(1, title="A", file_path=r"C:\music\Various\Song.mp3")
        b = _make_track(2, title="B", file_path=r"C:\music\Various\Song_639122324339578022.mp3")
        buckets = defaultdict(list, a=[a, b])
        result = self.job._build_filename_buckets(buckets=buckets, found_groups=set())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Integration: _scan_bucket + filename pass produces the expected finding
# ---------------------------------------------------------------------------


class TestFilenameBucketSurfacesFinding:
    """End-to-end check that the filename-bucket pass produces a
    `duplicate_tracks` finding for the Mushy scenario."""

    def test_seven_dedup_orphans_caught_as_one_group(self):
        job = DuplicateDetectorJob()
        ctx = _FakeContext()

        base_dir = "/data/torrents/music/Various Artists - Napoleon Dynamite OST"
        tracks = [
            _make_track(1, title="Heres Rico Musiq",
                        file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq.mp3"),
            *[_make_track(i + 2, title=f"unrelated parsed title {i}",
                          file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq_{ts}.mp3")
              for i, ts in enumerate([
                  "639122324339578022", "639126674226945470",
                  "639127689938113502", "639130174269948395",
                  "639132075826102610", "639132543802519474",
              ])],
        ]
        # All in one filename bucket (canonical stem matches across all 7).
        fname_buckets = job._build_filename_buckets(
            buckets=defaultdict(list, _=tracks),
            found_groups=set(),
        )
        bucket = next(iter(fname_buckets.values()))

        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        job._scan_bucket(
            bucket_tracks=bucket,
            require_metadata_match=False,  # filename match is the evidence
            title_threshold=0.85,
            artist_threshold=0.80,
            ignore_cross_album=False,
            found_groups=set(),
            processed_holder={'count': 0},
            total=7,
            result=result,
            context=ctx,
        )

        assert result.findings_created == 1
        finding = ctx.findings[0]
        assert finding['finding_type'] == 'duplicate_tracks'
        assert finding['details']['count'] == 7
        assert {t['id'] for t in finding['details']['tracks']} == {1, 2, 3, 4, 5, 6, 7}


# ---------------------------------------------------------------------------
# Negative coverage: title-pass behavior unchanged for normal scenarios
# ---------------------------------------------------------------------------


class TestExistingTitlePassUnchanged:
    """The new filename pass must not interfere with the original
    title-bucket logic — same album, same artist, similar titles still
    detected."""

    def test_same_track_two_different_files_in_same_album(self):
        job = DuplicateDetectorJob()
        ctx = _FakeContext()

        a = _make_track(1, title="Hello World", file_path="/lib/01_hello_world.mp3", bitrate=320)
        b = _make_track(2, title="Hello World", file_path="/lib/01-hello-world.mp3", bitrate=192)

        # title-pass bucket
        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        job._scan_bucket(
            bucket_tracks=[a, b],
            require_metadata_match=True,
            title_threshold=0.85,
            artist_threshold=0.80,
            ignore_cross_album=False,
            found_groups=set(),
            processed_holder={'count': 0},
            total=2,
            result=result,
            context=ctx,
        )
        assert result.findings_created == 1

    def test_cross_album_skip_still_honored_in_title_pass(self):
        """When `ignore_cross_album=True` the title pass must still skip
        same-title-different-album pairs. The filename pass would catch
        them only if the canonical filenames also matched, which they
        won't for legitimate cross-album cases (different folder paths)."""
        job = DuplicateDetectorJob()
        ctx = _FakeContext()

        a = _make_track(1, title="Hello World", album="Album A",
                        file_path="/lib/Artist/Album A/Hello World.mp3")
        b = _make_track(2, title="Hello World", album="Album B",
                        file_path="/lib/Artist/Album B/Hello World.mp3")

        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        job._scan_bucket(
            bucket_tracks=[a, b],
            require_metadata_match=True,
            title_threshold=0.85,
            artist_threshold=0.80,
            ignore_cross_album=True,
            found_groups=set(),
            processed_holder={'count': 0},
            total=2,
            result=result,
            context=ctx,
        )
        assert result.findings_created == 0
