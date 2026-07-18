"""#1047 — the 0.70-0.79 confidence band must count as matched in playlist sync.

The sync's finder accepts db matches at 0.7 (the app-wide "you own this"
bar), but MatchResult.is_match hardcoded 0.8 — so a 0.70-0.79 match resolved
to a LIVE server track and was still counted unmatched: sent to the wishlist
(re-download of something owned) and left off the server playlist. That band
is a real chunk of any large fuzzy-tagged library (part of the ~300-track
gap in the report).

MatchResult now carries its caller's threshold; the default stays 0.8 so
every other consumer is untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from core.matching_engine import MatchResult

_ROOT = Path(__file__).resolve().parent.parent.parent


def _mr(confidence, track=True, **kw):
    return MatchResult(
        spotify_track=SimpleNamespace(name="T"),
        plex_track=SimpleNamespace(title="T") if track else None,
        confidence=confidence,
        match_type="robust_search",
        **kw,
    )


class TestDefaultThresholdUnchanged:
    def test_default_bar_is_still_08(self):
        assert _mr(0.79).is_match is False
        assert _mr(0.80).is_match is True

    def test_no_track_never_matches(self):
        assert _mr(0.95, track=False).is_match is False


class TestSyncThreshold:
    def test_band_counts_as_matched_at_07(self):
        assert _mr(0.70, match_threshold=0.7).is_match is True
        assert _mr(0.75, match_threshold=0.7).is_match is True

    def test_below_the_finder_bar_still_unmatched(self):
        assert _mr(0.69, match_threshold=0.7).is_match is False

    def test_no_track_still_never_matches(self):
        assert _mr(0.75, track=False, match_threshold=0.7).is_match is False


def test_sync_service_constructs_with_the_finder_threshold():
    src = (_ROOT / "services" / "sync_service.py").read_text(encoding="utf-8",
                                                             errors="replace")
    assert "match_threshold=0.7" in src
    # and the finder really does accept 0.7 — the two must stay in agreement
    assert re.search(r"confidence_threshold=0\.7", src)
    assert "confidence >= 0.7" in src
