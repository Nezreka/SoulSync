"""Regression tests for two duplicate-detector bugs reported against
CJK-titled libraries and the "Ignore Cross-Album" setting.

Bug 1 — CJK titles all matched each other: ``_normalize`` stripped every
non-ASCII character, so any Japanese/Chinese/Korean title collapsed to
an empty string. Two empty strings score a perfect 1.0 SequenceMatcher
ratio, so e.g. Snail's House - "いつもの道" and Snail's House -
"寒い朝" (two different tracks, same artist) were flagged as
duplicates regardless of their actual titles.

Bug 2 — "Ignore Cross-Album" did nothing for filename-bucket matches:
the cross-album gate was only checked in the ``require_metadata_match``
(title-bucket) branch of ``_scan_bucket``. The filename-bucket pass
(``require_metadata_match=False``) never checked it, so e.g. a single
edit and an album version of the same track sharing an identical
filename (``02 - Clasp.flac``) were flagged as duplicates across
albums even with the setting enabled.
"""

from types import SimpleNamespace

from core.repair_jobs.duplicate_detector import DuplicateDetectorJob, _normalize
from tests.test_duplicate_detector_slskd_dedup import _FakeContext, _make_track


class TestNormalizePreservesCJK:
    def test_different_japanese_titles_score_low_similarity(self):
        """Distinct CJK titles must not collapse to the same normalized
        string — this was the root cause of the false-positive matches."""
        from difflib import SequenceMatcher

        a = _normalize("いつもの道")
        b = _normalize("寒い朝")
        assert a != ""
        assert b != ""
        assert a != b
        assert SequenceMatcher(None, a, b).ratio() < 0.85

    def test_same_japanese_title_still_matches(self):
        """Genuine duplicates with identical CJK titles must still be
        caught — the fix must not make CJK titles always mismatch."""
        from difflib import SequenceMatcher

        a = _normalize("いつもの道")
        b = _normalize("いつもの道")
        assert SequenceMatcher(None, a, b).ratio() == 1.0

    def test_ascii_titles_unaffected(self):
        assert _normalize("Hello World (Remix)") == "hello world (remix)"


class TestCrossAlbumHonoredInFilenamePass:
    def setup_method(self):
        self.job = DuplicateDetectorJob()

    def test_single_vs_album_version_not_flagged_when_ignored(self):
        """Throwing Snow - 'Clasp' as a single (Axioms) and as an album
        track (Glower) share the exact same filename. With
        ignore_cross_album=True this must NOT be flagged."""
        ctx = _FakeContext()
        single = _make_track(
            1, title="Clasp", artist="Throwing Snow", album="Axioms",
            file_path="/media/music/Throwing Snow/Axioms/02 - Clasp.flac",
            duration=200.0,
        )
        album_version = _make_track(
            2, title="Clasp", artist="Throwing Snow", album="Glower",
            file_path="/media/music/Throwing Snow/Glower/02 - Clasp.flac",
            duration=200.5,
        )

        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        self.job._scan_bucket(
            bucket_tracks=[single, album_version],
            require_metadata_match=False,
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

    def test_still_flagged_when_cross_album_not_ignored(self):
        """Same pair, but with the setting off (default) — should still
        be flagged, confirming the gate is additive, not a regression."""
        ctx = _FakeContext()
        single = _make_track(
            1, title="Clasp", artist="Throwing Snow", album="Axioms",
            file_path="/media/music/Throwing Snow/Axioms/02 - Clasp.flac",
            duration=200.0,
        )
        album_version = _make_track(
            2, title="Clasp", artist="Throwing Snow", album="Glower",
            file_path="/media/music/Throwing Snow/Glower/02 - Clasp.flac",
            duration=200.5,
        )

        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        self.job._scan_bucket(
            bucket_tracks=[single, album_version],
            require_metadata_match=False,
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

    def test_same_album_duplicates_still_flagged_when_ignoring_cross_album(self):
        """Sanity check: ignore_cross_album=True must not block same-
        album filename-bucket duplicates (the slskd dedup-orphan case)."""
        ctx = _FakeContext()
        base_dir = "/data/torrents/music/Various Artists - Napoleon Dynamite OST"
        canonical = _make_track(
            1, title="Heres Rico Musiq",
            file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq.mp3",
        )
        sibling = _make_track(
            2, title="Heres Rico Musiq Variant",
            file_path=f"{base_dir}/14-john_swihart-heres_rico-musiq_639122324339578022.mp3",
        )

        result = SimpleNamespace(scanned=0, findings_created=0, errors=0)
        self.job._scan_bucket(
            bucket_tracks=[canonical, sibling],
            require_metadata_match=False,
            title_threshold=0.85,
            artist_threshold=0.80,
            ignore_cross_album=True,
            found_groups=set(),
            processed_holder={'count': 0},
            total=2,
            result=result,
            context=ctx,
        )
        assert result.findings_created == 1
