"""HiFi/Monochrome preview detection.

Some Monochrome instances only have 30-second Tidal preview access for
usage=DOWNLOAD: the HLS variant playlist for a 220s track contains only ~30s
of segments (with #EXT-X-ENDLIST). Detect that at manifest time so HiFi
declines and the orchestrator falls through to a real source — instead of
downloading the 30s file and quarantining it after the fact.
"""

import sys
import types

import pytest

# hifi_client imports config/db at module load; stub the heavy bits if needed.
from core.hifi_client import hls_total_seconds, is_preview_playlist


_PREVIEW_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:4
#EXTINF:3.994,
seg1.mp4
#EXTINF:3.994,
seg2.mp4
#EXTINF:3.994,
seg3.mp4
#EXTINF:3.994,
seg4.mp4
#EXTINF:3.994,
seg5.mp4
#EXTINF:3.994,
seg6.mp4
#EXTINF:3.994,
seg7.mp4
#EXTINF:1.9,
seg8.mp4
#EXT-X-ENDLIST
"""


def test_total_seconds_sums_extinf():
    assert hls_total_seconds(_PREVIEW_PLAYLIST) == pytest.approx(29.86, abs=0.1)


def test_total_seconds_empty():
    assert hls_total_seconds("") == 0.0
    assert hls_total_seconds("#EXTM3U\nno segments\n") == 0.0


def test_preview_when_playlist_far_shorter_than_track():
    # 30s playlist for a 220s track → preview.
    assert is_preview_playlist(playlist_s=29.9, track_s=220) is True


def test_not_preview_when_playlist_matches_track():
    assert is_preview_playlist(playlist_s=218.0, track_s=220) is False


def test_not_preview_when_track_duration_unknown():
    # No reference → can't decide; don't false-positive (post-download guard
    # is the safety net).
    assert is_preview_playlist(playlist_s=29.9, track_s=0) is False
    assert is_preview_playlist(playlist_s=0, track_s=220) is False


def test_short_real_track_not_flagged():
    # A genuinely ~30s track whose playlist is ~30s is not a preview.
    assert is_preview_playlist(playlist_s=29.0, track_s=31) is False
