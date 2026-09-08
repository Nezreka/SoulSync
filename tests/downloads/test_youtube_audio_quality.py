"""YouTube music downloads: original stream vs re-encode.

YouTube does not serve MP3. With re-encode off, search claims Opus/AAC and
yt-dlp remuxes. With re-encode on (the product default: MP3 320), ranking
uses the file on disk. Tests that pin original-stream behavior stub
re-encode off via the autouse fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.download_plugins.types import TrackResult
from core.downloads import validation
from core.downloads.validation import _filter_youtube_by_quality, get_valid_candidates
from core.quality.model import AudioQuality, QualityTarget
from core import youtube_client
from core.youtube_client import (
    YouTubeClient,
    _youtube_transcode_settings as _real_youtube_transcode_settings,
    resolve_downloaded_audio_path,
    youtube_audio_format_selector,
    youtube_audio_postprocessor,
    youtube_audio_postprocessor_from_config,
    youtube_authenticated_extractor_args,
    youtube_probe_auth_label,
    youtube_quality_rank_band,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _bare_client():
    return YouTubeClient.__new__(YouTubeClient)


def _yt_track(**over):
    base = dict(
        username='youtube',
        filename='abc123||Song',
        size=0,
        bitrate=160,
        duration=180_000,
        quality='opus',
        free_upload_slots=999,
        upload_speed=999,
        queue_length=0,
        artist='Artist',
        title='Song',
    )
    base.update(over)
    return TrackResult(**base)


@dataclass
class _Expected:
    name: str = 'Song'
    artists: tuple = ('Artist',)
    duration_ms: int = 180_000
    album: str | None = None


class _MatchingEngine:
    def score_track_match(self, **kwargs):
        return 0.99, 'core_title_match'

    def normalize_string(self, text):
        return (text or '').lower()


def _use_tier(monkeypatch, tier: str):
    monkeypatch.setattr(youtube_client, 'youtube_requested_tier', lambda: tier)


@pytest.fixture(autouse=True)
def _reencode_off(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (False, 'mp3', '320'),
    )


# ── search-result quality stamp ────────────────────────────────────────────

def test_youtube_track_result_without_formats_claims_opus_160_not_mp3():
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
    )
    assert tr.quality == 'opus'
    assert tr.bitrate == 160
    assert tr.audio_quality.format == 'opus'
    assert tr.audio_quality.bitrate == 160


def test_youtube_track_result_with_cookies_still_claims_opus_160_without_formats(monkeypatch):
    monkeypatch.setattr(
        'core.youtube_client._resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
    )
    assert tr.quality == 'opus'
    assert tr.bitrate == 160


def test_cookies_plus_reencode_claim_mp3_320_not_premium_opus(monkeypatch):
    """Cookies must never be treated as Premium. Re-encode claims the
    converted file (MP3 320), not Opus 256."""
    monkeypatch.setattr(
        'core.youtube_client._resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
    )
    assert tr.quality == 'mp3'
    assert tr.bitrate == 320
    catalog = _bare_client()._ytmusic_hit_to_track_result({
        'id': 'vid1', 'name': 'Song', 'artists': ['Artist'],
    })
    assert catalog.quality == 'mp3'
    assert catalog.bitrate == 320


def test_youtube_track_result_stamps_opus_from_format_dict():
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Song', 'duration': 180},
        {'acodec': 'opus', 'abr': 160, 'ext': 'webm'},
    )
    assert tr.quality == 'opus'
    assert tr.bitrate == 160


def test_youtube_track_result_stamps_aac_from_m4a_format():
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Song', 'duration': 180},
        {'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a'},
    )
    assert tr.quality == 'aac'
    assert tr.bitrate == 128


# ── itag probe before ranking ──────────────────────────────────────────────

def _patch_ydl(monkeypatch, info=None, error=None, calls=None, by_video=None):
    class _FakeYoutubeDL:
        def __init__(self, opts):
            if calls is not None:
                calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            if error is not None:
                raise error
            if by_video is not None:
                video_id = url.rsplit('v=', 1)[-1]
                return by_video[video_id]
            return info

    monkeypatch.setattr(youtube_client.yt_dlp, 'YoutubeDL', _FakeYoutubeDL)


def test_refresh_stamps_opus_256_from_itag_774(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'opus_256')
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'opus'
    assert cand.bitrate == 256


def test_refresh_stamps_aac_256_from_itag_141(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'aac_256')
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'format_id': '140'},
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 256, 'ext': 'm4a', 'format_id': '141'},
        ],
    })
    cand = _yt_track(quality='opus', bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'aac'
    assert cand.bitrate == 256


def test_refresh_cookies_with_only_itag_251_stay_opus_160(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'opus'
    assert cand.bitrate == 160
    assert calls and 'cookiefile' in calls[0]


def test_refresh_probe_error_keeps_opus_160(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, error=RuntimeError('bot check'))
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'opus'
    assert cand.bitrate == 160


def test_refresh_empty_formats_keeps_opus_160(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {'formats': []})
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.bitrate == 160


def test_refresh_stamps_aac_128_from_itag_140(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'format_id': '140'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'aac'
    assert cand.bitrate == 128


def test_refresh_probes_only_top_limit(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    cands = [
        _yt_track(filename=f'vid{i}||Song')
        for i in range(4)
    ]
    for i, c in enumerate(cands):
        c.confidence = 0.9 - (i * 0.1)
    # Profile needs Opus 256 — keep walking until the budget, don't copy 160 onto leftovers.
    _bare_client().refresh_claimed_quality(
        cands, limit=3,
        targets=[QualityTarget(label='Opus 256', format='opus', min_bitrate=256)],
    )
    assert len(calls) == 3
    assert all(c.bitrate == 160 for c in cands[:3])
    assert cands[3].bitrate == 160


def test_refresh_default_stops_at_premium_ceiling(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    }, calls=calls)
    cands = [_yt_track(filename=f'vid{i}||Song') for i in range(4)]
    for i, c in enumerate(cands):
        c.confidence = 0.9 - (i * 0.1)
    client = _bare_client()
    client.refresh_claimed_quality(cands)
    assert len(calls) == 1
    assert cands[0].bitrate == 256
    assert all(c.bitrate == 160 for c in cands[1:])
    client.refresh_claimed_quality([cands[0]])
    assert len(calls) == 1  # same video-id cache; other videos were not stamped


def test_refresh_cache_avoids_second_extract_for_same_video(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    }, calls=calls)
    client = _bare_client()
    first = _yt_track(filename='abc123||Song')
    second = _yt_track(filename='abc123||Song')
    client.refresh_claimed_quality([first])
    client.refresh_claimed_quality([second])
    assert len(calls) == 1
    assert second.bitrate == 256


def test_refresh_probe_failure_does_not_stamp_siblings(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, error=RuntimeError('bot check'))
    cands = [_yt_track(filename=f'vid{i}||Song') for i in range(3)]
    _bare_client().refresh_claimed_quality(cands)
    assert all(c.bitrate == 160 for c in cands)


def test_refresh_does_not_copy_premium_itag_onto_other_videos(monkeypatch):
    """Account-level cookies are not per-video. Video B must not inherit
    itag 774 from video A when re-encode is off."""
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    })
    premium = _yt_track(filename='aaaaaaaaaaa||Premium')
    other = _yt_track(filename='bbbbbbbbbbb||Other')
    premium.confidence = 0.9
    other.confidence = 0.8
    _bare_client().refresh_claimed_quality([premium, other], limit=1)
    assert premium.bitrate == 256
    assert other.quality == 'opus'
    assert other.bitrate == 160


def test_refresh_lookahead_picks_better_itag_on_second_video(monkeypatch):
    """Same recording, worse title match, better itag — one extra player fetch."""
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, calls=calls, by_video={
        'aaaaaaaaaaa': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ]},
        'bbbbbbbbbbb': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ]},
        'ccccccccccc': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ]},
    })
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    better = _yt_track(filename='bbbbbbbbbbb||Song')
    leftover = _yt_track(filename='ccccccccccc||Song')
    top.confidence = 0.9
    better.confidence = 0.8
    leftover.confidence = 0.7
    _bare_client().refresh_claimed_quality(
        [top, better, leftover],
        targets=[QualityTarget(label='Opus', format='opus')],
    )
    assert len(calls) == 2
    assert top.bitrate == 160
    assert better.bitrate == 256
    assert leftover.bitrate == 160


def test_refresh_stops_when_youtube_cannot_meet_profile(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    cands = [_yt_track(filename=f'vid{i}||Song') for i in range(4)]
    _bare_client().refresh_claimed_quality(
        cands,
        targets=[QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)],
    )
    assert len(calls) == 1


def test_reload_settings_clears_stale_probe_claims(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    monkeypatch.setattr(
        'core.settings.config_manager.get',
        lambda key, default=None: default,
    )
    client = _bare_client()
    client.download_opts = {}
    client.download_path = tmp_path
    client._yt_probe_quality = {'vid1': AudioQuality(format='mp3', bitrate=320)}
    client._yt_probe_info = {'vid1': (0.0, {'id': 'vid1'})}
    client.reload_settings()
    assert client._probe_quality_cache() == {}
    assert client._probe_info_cache() == {}


def test_probe_quality_cache_expires_and_reprobes(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    client = _bare_client()
    now = {'t': 1_000.0}
    monkeypatch.setattr(youtube_client.time, 'monotonic', lambda: now['t'])
    client.refresh_claimed_quality([_yt_track(filename='abc123||Song')])
    assert len(calls) == 1
    now['t'] += client._PROBE_INFO_TTL_S + 1
    client.refresh_claimed_quality([_yt_track(filename='abc123||Song')])
    assert len(calls) == 2


def test_video_id_from_url():
    assert YouTubeClient._video_id_from_url('https://www.youtube.com/watch?v=jNQXAC9IVRw') == 'jNQXAC9IVRw'
    assert YouTubeClient._video_id_from_url('https://youtu.be/jNQXAC9IVRw') == 'jNQXAC9IVRw'
    assert YouTubeClient._video_id_from_url('https://www.youtube.com/shorts/jNQXAC9IVRw') == 'jNQXAC9IVRw'
    assert YouTubeClient._video_id_from_url('') == ''
    assert YouTubeClient._video_id_from_url('https://example.com') == ''


def test_refresh_stamps_aac_128_when_profile_asks_aac_128(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'aac_128')
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'format_id': '140'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'aac'
    assert cand.bitrate == 128


# ── yt-dlp postprocessor ───────────────────────────────────────────────────

def test_postprocessor_default_is_remux_not_mp3_320():
    pp = youtube_audio_postprocessor()
    assert pp['key'] == 'FFmpegExtractAudio'
    assert pp['preferredcodec'] == 'best'
    assert 'preferredquality' not in pp


def test_postprocessor_transcode_uses_configured_codec_bitrate():
    pp = youtube_audio_postprocessor(transcode=True, codec='mp3', bitrate='320')
    assert pp['preferredcodec'] == 'mp3'
    assert pp['preferredquality'] == '320'


def test_postprocessor_from_config_respects_live_settings(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (False, 'mp3', '320'),
    )
    pp = youtube_audio_postprocessor_from_config()
    assert pp['preferredcodec'] == 'best'

    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'aac', '192'),
    )
    pp = youtube_audio_postprocessor_from_config()
    assert pp['preferredcodec'] == 'aac'
    assert pp['preferredquality'] == '192'


def test_download_sync_does_not_hardcode_mp3_suffix(tmp_path, monkeypatch):
    """_download_sync must resolve the real extracted path, not Title.mp3."""
    opus_path = tmp_path / 'Song.opus'
    opus_path.write_bytes(b'opus')

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            return {'title': 'Song', 'ext': 'webm', 'filepath': str(opus_path)}
        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )

    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    result = client._download_sync('https://www.youtube.com/watch?v=abc', 'Song')
    assert result == str(opus_path)
    assert not result.endswith('.mp3')


# ── resolve_downloaded_audio_path ──────────────────────────────────────────

def test_resolve_prefers_info_filepath(tmp_path):
    final = tmp_path / 'Song.opus'
    final.write_bytes(b'x')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(tmp_path / 'Song.webm')
    assert resolve_downloaded_audio_path(ydl, {'filepath': str(final)}) == str(final)


def test_resolve_falls_back_to_sibling_audio_ext(tmp_path):
    audio = tmp_path / 'Song.m4a'
    audio.write_bytes(b'x')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(tmp_path / 'Song.webm')
    assert resolve_downloaded_audio_path(ydl, {}) == str(audio)


def test_resolve_prefers_converted_audio_over_leftover_webm(tmp_path):
    """yt-dlp may leave the DASH .webm beside the extracted .mp3/.opus."""
    webm = tmp_path / 'Song.webm'
    mp3 = tmp_path / 'Song.mp3'
    webm.write_bytes(b'container')
    mp3.write_bytes(b'converted')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(webm)
    assert resolve_downloaded_audio_path(ydl, {'filepath': str(webm)}) == str(mp3)


def test_discard_leftover_container_beside_extracted_audio(tmp_path):
    webm = tmp_path / 'Song.webm'
    mp4 = tmp_path / 'Song.mp4'
    mp3 = tmp_path / 'Song.mp3'
    webm.write_bytes(b'container')
    mp4.write_bytes(b'muxed')
    mp3.write_bytes(b'converted')
    discard = getattr(youtube_client, 'discard_leftover_youtube_containers', None)
    assert callable(discard)
    discard(str(mp3))
    assert mp3.exists()
    assert not webm.exists()
    assert not mp4.exists()


# ── pre-download quality filter ────────────────────────────────────────────

def test_youtube_quality_filter_rejects_when_fallback_off(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)], False),
    )
    cand = _yt_track()
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == []


def test_youtube_quality_filter_keeps_via_fallback(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)], True),
    )
    cand = _yt_track()
    result = get_valid_candidates([cand], _Expected(), 'Artist Song')
    assert result == [cand]


def test_youtube_quality_filter_keeps_when_opus_is_a_target(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='Opus', format='opus')], False),
    )
    cand = _yt_track()
    result = get_valid_candidates([cand], _Expected(), 'Artist Song')
    assert result == [cand]


def test_transcode_on_does_not_make_search_look_like_mp3(monkeypatch):
    """Re-encode off: search still claims Opus 160, so a FLAC+MP3 profile
    with Fallback off still rejects YouTube."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([
            QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16),
            QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320),
        ], False),
    )
    cand = _yt_track()
    assert cand.quality == 'opus'
    assert cand.bitrate == 160
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == []


def test_mp3_only_profile_skips_youtube_when_reencode_off(monkeypatch):
    """MP3 320 only + Fallback off + remux: YouTube is not chosen."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320)], False),
    )
    cand = _yt_track()
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == []


def test_mp3_only_profile_keeps_youtube_only_as_fallback(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320)], True),
    )
    cand = _yt_track()
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == [cand]


def test_reencode_to_mp3_makes_youtube_match_mp3_only_profile(monkeypatch):
    """Re-encode on: ranking uses the converted MP3 320, so an MP3-only
    profile with Fallback off accepts YouTube. The fetch is still best Opus."""
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320)], False),
    )
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
    )
    assert tr.quality == 'mp3'
    assert tr.bitrate == 320
    assert get_valid_candidates([tr], _Expected(), 'Artist Song') == [tr]


def test_reencode_to_mp3_still_rejected_by_flac_only_profile(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)], False),
    )
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
    )
    assert get_valid_candidates([tr], _Expected(), 'Artist Song') == []


def test_reencode_probe_stamps_output_not_original_itag(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'mp3'
    assert cand.bitrate == 320


def test_reencode_probe_with_cookies_and_only_itag_251_is_still_mp3_not_256(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(
        youtube_client, '_resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'mp3'
    assert cand.bitrate == 320


def test_reencode_probe_failure_keeps_search_mp3_claim(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, error=RuntimeError('bot check'))
    cand = _yt_track(quality='mp3', bitrate=320)
    cand.set_quality(AudioQuality(format='mp3', bitrate=320))
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'mp3'
    assert cand.bitrate == 320


def test_reencode_siblings_inherit_converted_claim_not_original_itag(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'aac', '256'),
    )
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    }, calls=calls)
    cands = [_yt_track(filename=f'vid{i}||Song') for i in range(3)]
    _bare_client().refresh_claimed_quality(cands)
    assert len(calls) == 1
    assert all(c.quality == 'aac' and c.bitrate == 256 for c in cands)


def test_refresh_probe_forwards_cookie_opts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        youtube_client, '_resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    _bare_client().refresh_claimed_quality([_yt_track()])
    assert calls[0].get('cookiefile') == '/tmp/youtube_cookies.txt'


def test_authenticated_extractor_args_asks_web_music_only_with_cookies():
    """itag 774/141 live on the Music player. Cookies are not Premium —
    this only adds the client that *can* return those itags."""
    assert youtube_authenticated_extractor_args({}) == {}
    assert youtube_authenticated_extractor_args({'quiet': True}) == {}
    music = youtube_authenticated_extractor_args(
        {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    assert music['youtube']['player_client'] == ['web_music', 'default']
    browser = youtube_authenticated_extractor_args(
        {'cookiesfrombrowser': ('firefox',)},
    )
    assert browser['youtube']['player_client'] == ['web_music', 'default']


def test_probe_auth_label_never_includes_paths_or_secrets():
    assert youtube_probe_auth_label({}) == 'none'
    assert youtube_probe_auth_label({'quiet': True}) == 'none'
    assert youtube_probe_auth_label(
        {'cookiefile': '/secret/youtube_cookies.txt'},
    ) == 'cookiefile'
    assert youtube_probe_auth_label(
        {'cookiesfrombrowser': ('firefox',)},
    ) == 'browser:firefox'
    assert 'secret' not in youtube_probe_auth_label(
        {'cookiefile': '/secret/youtube_cookies.txt'},
    )


def test_refresh_probe_requests_web_music_when_cookies(monkeypatch):
    calls = []
    monkeypatch.setattr(
        youtube_client, '_resolve_cookie_opts',
        lambda: {'cookiefile': '/tmp/youtube_cookies.txt'},
    )
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    _bare_client().refresh_claimed_quality([_yt_track()])
    clients = (calls[0].get('extractor_args') or {}).get('youtube', {}).get('player_client')
    assert clients == ['web_music', 'default']


def test_refresh_probe_skips_web_music_when_anonymous(monkeypatch):
    calls = []
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ],
    }, calls=calls)
    _bare_client().refresh_claimed_quality([_yt_track()])
    clients = (calls[0].get('extractor_args') or {}).get('youtube', {}).get('player_client', [])
    assert 'web_music' not in clients


def _patch_profile(monkeypatch, targets, fallback):
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: (list(targets), fallback),
    )


_FLAC = QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)
_MP3_320 = QualityTarget(label='MP3 320kbps', format='mp3', min_bitrate=320)
_MP3_192 = QualityTarget(label='MP3 192kbps', format='mp3', min_bitrate=192)
_AAC = QualityTarget(label='AAC', format='aac')
_AAC_256 = QualityTarget(label='AAC 256kbps', format='aac', min_bitrate=256)
_AAC_128 = QualityTarget(label='AAC 128kbps', format='aac', min_bitrate=128)
_OPUS = QualityTarget(label='Opus', format='opus')
_OPUS_128 = QualityTarget(label='Opus 128kbps', format='opus', min_bitrate=128)
_OPUS_192 = QualityTarget(label='Opus 192kbps', format='opus', min_bitrate=192)


def test_quality_filter_prefers_second_video_with_better_itag(monkeypatch):
    """Match-passing Video B with Opus 256 beats a better-titled 160."""
    _patch_profile(monkeypatch, [_OPUS], False)
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, by_video={
        'aaaaaaaaaaa': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ]},
        'bbbbbbbbbbb': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ]},
    })

    class _Orch:
        def client(self, name):
            return _bare_client()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    weaker = _yt_track(filename='aaaaaaaaaaa||Song')
    better = _yt_track(filename='bbbbbbbbbbb||Song')
    result = get_valid_candidates([weaker, better], _Expected(), 'Artist Song')
    assert result[0] is better
    assert better.bitrate == 256


def test_quality_rank_band_excludes_distant_confidence():
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    close = _yt_track(filename='bbbbbbbbbbb||Song')
    far = _yt_track(filename='ccccccccccc||Song')
    top.confidence = 0.92
    close.confidence = 0.88
    far.confidence = 0.62
    assert youtube_quality_rank_band([top, close, far]) == [top, close]


def test_quality_rank_band_uses_match_confidence_from_orchestrator():
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    far = _yt_track(filename='bbbbbbbbbbb||Song')
    top._match_confidence = 0.92
    far._match_confidence = 0.62
    assert youtube_quality_rank_band([top, far]) == [top]


def test_quality_rank_band_includes_score_exactly_on_margin():
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    edge = _yt_track(filename='bbbbbbbbbbb||Song')
    below = _yt_track(filename='ccccccccccc||Song')
    top.confidence = 0.92
    edge.confidence = 0.87
    below.confidence = 0.869
    assert youtube_quality_rank_band([top, edge, below]) == [top, edge]


def test_cached_quality_ignores_legacy_nontuple_entry():
    """Pre-TTL cache stored AudioQuality bare. Lookup must not crash."""
    client = _bare_client()
    client._yt_probe_quality = {
        'vid1': AudioQuality(format='opus', bitrate=256),
    }
    assert client._cached_quality('vid1') is None
    assert client._probe_quality_cache().get('vid1') is None


def test_quality_filter_does_not_promote_distant_better_itag(monkeypatch):
    """A 0.62 cover at Opus 256 must not beat a 0.92 match at 160."""
    _patch_profile(monkeypatch, [_OPUS], False)
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, by_video={
        'aaaaaaaaaaa': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ]},
        'bbbbbbbbbbb': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ]},
    })

    class _Orch:
        def client(self, name):
            return _bare_client()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    far = _yt_track(filename='bbbbbbbbbbb||Song')
    top.confidence = 0.92
    far.confidence = 0.62
    result = _filter_youtube_by_quality([top, far])
    assert result[0] is top
    assert far not in result


def test_quality_filter_promotes_close_better_itag(monkeypatch):
    """0.88 vs 0.92 is close enough that the better encode can win."""
    _patch_profile(monkeypatch, [_OPUS], False)
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, by_video={
        'aaaaaaaaaaa': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'},
        ]},
        'bbbbbbbbbbb': {'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ]},
    })

    class _Orch:
        def client(self, name):
            return _bare_client()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    top = _yt_track(filename='aaaaaaaaaaa||Song')
    close = _yt_track(filename='bbbbbbbbbbb||Song')
    top.confidence = 0.92
    close.confidence = 0.88
    result = _filter_youtube_by_quality([top, close])
    assert result[0] is close
    assert close.bitrate == 256


@pytest.mark.parametrize("codec,bitrate,targets,fallback,kept", [
    ('mp3', '320', [_MP3_320], False, True),
    ('mp3', '192', [_MP3_320], False, False),
    ('mp3', '320', [_MP3_192], False, True),
    ('aac', '256', [_MP3_320], False, False),
    ('aac', '256', [_AAC_256], False, True),
    ('opus', '160', [_OPUS], False, True),
    ('mp3', '320', [_OPUS], False, False),
    ('mp3', '320', [_FLAC], False, False),
    ('mp3', '320', [_FLAC, _MP3_320], False, True),
    ('opus', '256', [_OPUS_192], False, True),
])
def test_reencode_ranking_follows_converted_file_not_stream(
    monkeypatch, codec, bitrate, targets, fallback, kept,
):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, codec, bitrate),
    )
    _patch_profile(monkeypatch, targets, fallback)
    tr = _bare_client()._youtube_to_track_result(
        {'id': 'abc123', 'title': 'Artist - Song', 'duration': 180},
        {'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
    )
    result = get_valid_candidates([tr], _Expected(), 'Artist Song')
    assert (result == [tr]) is kept


@pytest.mark.parametrize("quality,bitrate,targets,fallback,kept", [
    # Default balanced-style ladder (FLAC + MP3): YouTube is fallback-only
    ('opus', 160, [_FLAC, _MP3_320, _MP3_192], False, False),
    ('opus', 160, [_FLAC, _MP3_320, _MP3_192], True, True),
    ('aac', 128, [_FLAC, _MP3_320], False, False),
    ('aac', 128, [_FLAC, _MP3_320], True, True),
    # Audiophile FLAC-only
    ('opus', 160, [_FLAC], False, False),
    ('aac', 256, [_FLAC], False, False),
    # Explicit Opus / AAC targets — YouTube counts as a match
    ('opus', 160, [_FLAC, _OPUS], False, True),
    ('opus', 160, [_OPUS], False, True),
    ('opus', 160, [_OPUS_128], False, True),
    ('opus', 160, [_OPUS_192], False, False),  # 160 < 192
    ('opus', 256, [_OPUS_192], False, True),   # Premium itag 774 meets 192 floor
    ('opus', 160, [_OPUS_192], True, True),
    ('aac', 128, [_AAC], False, True),
    ('aac', 128, [_AAC_128], False, True),
    ('aac', 128, [_AAC_256], False, False),  # Premium itag 141 would pass; 140 does not
    ('aac', 256, [_AAC_256], False, True),
    ('aac', 128, [_FLAC, _AAC, _MP3_320], False, True),
    # Format mismatch: high-bitrate Opus is still not MP3
    ('opus', 256, [_MP3_320], False, False),
    ('opus', 320, [_MP3_320], False, False),
    ('aac', 256, [_MP3_320], False, False),
    # Empty target list = no constraint
    ('opus', 160, [], False, True),
    ('opus', 160, [], True, True),
    # Space-saver MP3-only
    ('opus', 160, [_MP3_192], False, False),
    ('aac', 128, [_MP3_192], True, True),
])
def test_youtube_quality_filter_matrix(monkeypatch, quality, bitrate, targets, fallback, kept):
    _patch_profile(monkeypatch, targets, fallback)
    cand = _yt_track(quality=quality, bitrate=bitrate)
    result = get_valid_candidates([cand], _Expected(), 'Artist Song')
    if kept:
        assert result == [cand]
    else:
        assert result == []


def test_youtube_quality_filter_prefers_higher_opus_bitrate(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS], False)
    low = _yt_track(filename='low||Song', bitrate=50, title='Song')
    high = _yt_track(filename='high||Song', bitrate=160, title='Song')
    result = get_valid_candidates([low, high], _Expected(), 'Artist Song')
    assert result[0] is high
    assert low in result and high in result


def test_quality_filter_probes_itag_before_rank(monkeypatch):
    """A probed Premium itag 774 (Opus 256) satisfies Opus ≥192; search-time 160 would not."""
    _patch_profile(monkeypatch, [_OPUS_192], False)

    class _Orch:
        def client(self, name):
            assert name == 'youtube'
            return _bare_client()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    })
    cand = _yt_track(bitrate=160)
    result = get_valid_candidates([cand], _Expected(), 'Artist Song')
    assert result == [cand]
    assert cand.bitrate == 256


def test_quality_filter_rejects_unprobed_160_when_opus_192_required(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS_192], False)
    cand = _yt_track(bitrate=160)
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == []


def test_tidal_is_not_quality_filtered_by_youtube_path(monkeypatch):
    """Tidal already requested a profile tier — do not run the YouTube filter."""
    monkeypatch.setattr(validation, 'matching_engine', _MatchingEngine())
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([_FLAC], False),
    )
    tidal = _yt_track(username='tidal', quality='flac', bitrate=1411)
    # TrackResult needs bit_depth for FLAC match; set_quality isn't required
    # because the youtube filter is skipped for non-youtube usernames.
    result = get_valid_candidates([tidal], _Expected(), 'Artist Song')
    assert result == [tidal]


def test_quality_filter_does_not_band_non_youtube_in_hybrid_pool(monkeypatch):
    """A closer YouTube hit must not drop a Tidal match in a mixed best-quality pool."""
    _patch_profile(monkeypatch, [_OPUS], False)
    probed = []

    class _YT:
        def refresh_claimed_quality(self, candidates, **kwargs):
            probed.extend(candidates)

    class _Orch:
        def client(self, name):
            return _YT()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    yt_top = _yt_track(filename='aaaaaaaaaaa||Song')
    yt_far = _yt_track(filename='bbbbbbbbbbb||Other', title='Other')
    tidal = _yt_track(
        username='tidal', filename='tid||Song', quality='flac', bitrate=1411,
    )
    yt_top.confidence = 0.92
    yt_far.confidence = 0.62
    tidal.confidence = 0.70
    result = _filter_youtube_by_quality([yt_top, yt_far, tidal])
    assert tidal in result
    assert yt_top in result
    assert yt_far not in result
    assert probed == [yt_top]


def test_quality_filter_keeps_tidal_when_youtube_misses_profile(monkeypatch):
    """FLAC-only + remux: rejected YouTube rows must not wipe a Tidal hit."""
    _patch_profile(monkeypatch, [_FLAC], False)
    yt = _yt_track()
    yt.confidence = 0.92
    tidal = _yt_track(
        username='tidal', filename='tid||Song', quality='flac', bitrate=1411,
    )
    tidal.confidence = 0.70
    result = _filter_youtube_by_quality([yt, tidal])
    assert tidal in result
    assert yt not in result


def test_get_valid_candidates_keeps_tidal_in_youtube_first_hybrid_pool(monkeypatch):
    """Worker path: search_all_sources can list YouTube first, then Tidal."""
    _patch_profile(monkeypatch, [_OPUS], False)

    class _Scores:
        def score_track_match(self, **kw):
            if kw.get('candidate_title') == 'Other':
                return 0.62, 'ok'
            if kw.get('candidate_duration_ms') == 181_000:
                return 0.70, 'ok'
            return 0.92, 'ok'

        def normalize_string(self, text):
            return (text or '').lower()

    monkeypatch.setattr(validation, 'matching_engine', _Scores())
    yt_top = _yt_track(filename='aaaaaaaaaaa||Song')
    yt_far = _yt_track(filename='bbbbbbbbbbb||Other', title='Other')
    tidal = _yt_track(
        username='tidal', filename='tid||Song', quality='flac', bitrate=1411,
        duration=181_000,
    )
    result = get_valid_candidates([yt_top, yt_far, tidal], _Expected(), 'Artist Song')
    assert tidal in result
    assert yt_top in result
    assert yt_far not in result


def test_get_valid_candidates_filters_youtube_when_tidal_is_first(monkeypatch):
    """Tidal-first pool must still band/probe YouTube without dropping Tidal."""
    _patch_profile(monkeypatch, [_OPUS], False)

    class _Scores:
        def score_track_match(self, **kw):
            if kw.get('candidate_title') == 'Other':
                return 0.62, 'ok'
            if kw.get('candidate_duration_ms') == 181_000:
                return 0.70, 'ok'
            return 0.92, 'ok'

        def normalize_string(self, text):
            return (text or '').lower()

    monkeypatch.setattr(validation, 'matching_engine', _Scores())
    yt_top = _yt_track(filename='aaaaaaaaaaa||Song')
    yt_far = _yt_track(filename='bbbbbbbbbbb||Other', title='Other')
    tidal = _yt_track(
        username='tidal', filename='tid||Song', quality='flac', bitrate=1411,
        duration=181_000,
    )
    result = get_valid_candidates([tidal, yt_top, yt_far], _Expected(), 'Artist Song')
    assert tidal in result
    assert yt_top in result
    assert yt_far not in result


def test_get_valid_candidates_probes_youtube_when_soulseek_is_first(monkeypatch):
    """Soulseek-first hybrid pool must still probe YouTube and keep the peer."""
    _patch_profile(monkeypatch, [_OPUS], False)
    probed = []
    slsk_batches = []

    class _YT:
        def refresh_claimed_quality(self, candidates, **kwargs):
            probed.extend(candidates)

    class _Slsk:
        def filter_results_by_quality_preference(self, cands, profile_id=None):
            slsk_batches.append([getattr(c, 'username', None) for c in cands])
            return list(cands)

    class _Orch:
        def client(self, name):
            if name == 'youtube':
                return _YT()
            if name == 'soulseek':
                return _Slsk()
            return None

    class _Engine:
        def score_track_match(self, **kwargs):
            return 0.99, 'ok'

        def normalize_string(self, text):
            return (text or '').lower()

        def find_best_slskd_matches_enhanced(self, track, results, **_kw):
            return list(results)

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, 'matching_engine', _Engine())
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})

    peer = TrackResult(
        username='alice',
        filename='Artist/Album/01 - Song.flac',
        size=1,
        bitrate=1411,
        duration=180_000,
        quality='flac',
        free_upload_slots=1,
        upload_speed=1,
        queue_length=0,
        artist='Artist',
        title='Song',
    )
    yt = _yt_track(filename='aaaaaaaaaaa||Song')
    result = get_valid_candidates([peer, yt], _Expected(), 'Artist Song')
    assert peer in result
    assert yt in result
    assert probed == [yt]
    assert slsk_batches == [['alice']]


def test_mixed_pool_walk_picks_youtube_774_over_soulseek_flac(monkeypatch):
    """Worker path: probed itag 774 must be tried before a Soulseek FLAC when
    Opus ≥192 is the top target — even if the walk is not flagged quality_first.
    """
    from core.downloads.candidates import order_candidates

    _patch_profile(monkeypatch, [
        _OPUS_192,
        QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16),
    ], False)

    class _YT:
        def refresh_claimed_quality(self, candidates, **kwargs):
            for c in candidates:
                c.set_quality(AudioQuality('opus', bitrate=256))

    class _Slsk:
        def filter_results_by_quality_preference(self, cands, profile_id=None):
            return list(cands)

    class _Orch:
        def client(self, name):
            if name == 'youtube':
                return _YT()
            if name == 'soulseek':
                return _Slsk()
            return None

    class _Engine:
        def score_track_match(self, **kwargs):
            return 0.85, 'ok'

        def normalize_string(self, text):
            return (text or '').lower()

        def find_best_slskd_matches_enhanced(self, track, results, **_kw):
            for r in results:
                r.confidence = 0.99
            return list(results)

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, 'matching_engine', _Engine())

    peer = TrackResult(
        username='alice',
        filename='Artist/Album/01 - Song.flac',
        size=20_000_000,
        bitrate=1411,
        duration=180_000,
        quality='flac',
        free_upload_slots=1,
        upload_speed=1,
        queue_length=0,
        artist='Artist',
        title='Song',
        sample_rate=44100,
        bit_depth=16,
    )
    yt = _yt_track(filename='aaaaaaaaaaa||Song', bitrate=160)
    result = get_valid_candidates([peer, yt], _Expected(), 'Artist Song')
    ordered = order_candidates(
        result, quality_first=False,
        targets=[_OPUS_192, QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)],
        source_order=['youtube', 'soulseek'],
    )
    assert ordered[0] is yt
    assert yt.bitrate == 256


def test_stubs_without_audio_quality_are_not_rejected():
    """Match-only test doubles omit audio_quality — filter must pass through."""
    from core.downloads.validation import _filter_youtube_by_quality

    class _Stub:
        username = 'youtube'
        title = 'Song'

    stub = _Stub()
    assert _filter_youtube_by_quality([stub]) == [stub]


def test_filter_empty_candidates():
    from core.downloads.validation import _filter_youtube_by_quality
    assert _filter_youtube_by_quality([]) == []


# ── postprocessor combinations ─────────────────────────────────────────────

@pytest.mark.parametrize("codec,bitrate,want_codec,want_br", [
    ('mp3', '320', 'mp3', '320'),
    ('mp3', '192', 'mp3', '192'),
    ('mp3', '128', 'mp3', '128'),
    ('opus', '256', 'opus', '256'),
    ('opus', '160', 'opus', '160'),
    ('aac', '256', 'aac', '256'),
    ('aac', '192', 'aac', '192'),
    ('m4a', '128', 'aac', '128'),
    ('M4A', '320', 'aac', '320'),
    ('ogg', '192', 'ogg', '192'),
    ('flac', '320', 'mp3', '320'),  # unknown codec → mp3
    ('', '320', 'mp3', '320'),
    (None, None, 'mp3', '320'),
    ('mp3', '0', 'mp3', '320'),
    ('mp3', '-5', 'mp3', '320'),
    ('mp3', 'bad', 'mp3', '320'),
])
def test_postprocessor_transcode_codec_bitrate_matrix(codec, bitrate, want_codec, want_br):
    pp = youtube_audio_postprocessor(transcode=True, codec=codec, bitrate=bitrate)
    assert pp['preferredcodec'] == want_codec
    assert pp['preferredquality'] == want_br
    assert pp['key'] == 'FFmpegExtractAudio'


@pytest.mark.parametrize("codec,bitrate", [
    ('mp3', '320'),
    ('opus', '256'),
    ('aac', '128'),
])
def test_postprocessor_ignores_codec_when_transcode_off(codec, bitrate):
    pp = youtube_audio_postprocessor(transcode=False, codec=codec, bitrate=bitrate)
    assert pp == {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'}


def test_postprocessor_from_config_defaults_to_mp3_320_when_keys_missing(monkeypatch):
    monkeypatch.setattr(
        'core.settings.config_manager.get',
        lambda key, default=None: default,
    )
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        _real_youtube_transcode_settings,
    )
    pp = youtube_audio_postprocessor_from_config()
    assert pp['preferredcodec'] == 'mp3'
    assert pp['preferredquality'] == '320'


def test_postprocessor_from_config_honours_explicit_off(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (False, 'mp3', '320'),
    )
    pp = youtube_audio_postprocessor_from_config()
    assert pp == {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'}


# ── path resolver edge cases ───────────────────────────────────────────────

def test_resolve_prefers_requested_downloads_over_prepare(tmp_path):
    final = tmp_path / 'out.opus'
    final.write_bytes(b'x')
    decoy = tmp_path / 'Song.webm'
    decoy.write_bytes(b'y')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(decoy)
    info = {'requested_downloads': [{'filepath': str(final)}]}
    assert resolve_downloaded_audio_path(ydl, info) == str(final)


def test_resolve_uses_underscore_filename(tmp_path):
    final = tmp_path / 'Song.opus'
    final.write_bytes(b'x')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(tmp_path / 'missing.webm')
    assert resolve_downloaded_audio_path(ydl, {'_filename': str(final)}) == str(final)


def test_resolve_returns_none_when_nothing_on_disk(tmp_path):
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(tmp_path / 'Song.webm')
    assert resolve_downloaded_audio_path(ydl, {}) is None


def test_resolve_survives_prepare_filename_raising(tmp_path):
    final = tmp_path / 'ok.m4a'
    final.write_bytes(b'x')
    ydl = MagicMock()
    ydl.prepare_filename.side_effect = RuntimeError('boom')
    assert resolve_downloaded_audio_path(ydl, {'filepath': str(final)}) == str(final)


def test_resolve_non_dict_info_uses_prepare(tmp_path):
    audio = tmp_path / 'Song.opus'
    audio.write_bytes(b'x')
    ydl = MagicMock()
    ydl.prepare_filename.return_value = str(tmp_path / 'Song.webm')
    assert resolve_downloaded_audio_path(ydl, None) == str(audio)


# ── download_sync edge cases ───────────────────────────────────────────────

def test_download_sync_resolves_m4a(tmp_path, monkeypatch):
    m4a = tmp_path / 'Song.m4a'
    m4a.write_bytes(b'aac')

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            return {'title': 'Song', 'ext': 'm4a', 'filepath': str(m4a)}
        def prepare_filename(self, info):
            return str(tmp_path / 'Song.m4a')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    assert client._download_sync('https://www.youtube.com/watch?v=abc', 'Song') == str(m4a)


def test_download_sync_returns_none_when_file_missing(tmp_path, monkeypatch):
    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            return {'title': 'Song', 'ext': 'webm'}
        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    assert client._download_sync('https://youtu.be/abc', 'Song') is None


def test_download_sync_aborts_on_shutdown(monkeypatch):
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = lambda: True
    assert client._download_sync('https://youtu.be/abc', 'Song') is None


def test_download_sync_rebuilds_postprocessor_from_live_config(tmp_path, monkeypatch):
    opus = tmp_path / 'Song.opus'
    opus.write_bytes(b'x')
    seen = {}

    class _FakeYDL:
        def __init__(self, opts):
            seen['pp'] = opts.get('postprocessors')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            return {'filepath': str(opus)}
        def prepare_filename(self, info):
            return str(opus)

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}],
    }
    client.shutdown_check = None
    client._download_sync('https://youtu.be/abc', 'Song')
    assert seen['pp'] == [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'}]


def test_download_sync_uses_preferred_audio_format_selector(tmp_path, monkeypatch):
    seen = {}
    audio = tmp_path / 'Song.opus'
    audio.write_bytes(b'x')

    class _FakeYDL:
        def __init__(self, opts):
            seen['format'] = opts.get('format')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            return {'filepath': str(audio)}
        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    monkeypatch.setattr(
        'core.youtube_client.youtube_requested_tier',
        lambda: 'opus_160',
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    client._download_sync('https://youtu.be/abc', 'Song')
    assert 'opus' in seen['format']


def test_download_sync_reencode_fetches_best_opus_not_profile_rung(tmp_path, monkeypatch):
    """Re-encode on: ignore an AAC 128 profile rung and fetch best original."""
    seen = {}
    audio = tmp_path / 'Song.mp3'
    audio.write_bytes(b'x')

    class _FakeYDL:
        def __init__(self, opts):
            seen['format'] = opts.get('format')

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'},
    )
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    monkeypatch.setattr(
        'core.youtube_client.quality_tier_for_source',
        lambda *a, **k: 'aac_128',
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    client._download_sync('https://youtu.be/abc', 'Song')
    assert 'abr>=192' in seen['format']
    assert seen['format'] == youtube_audio_format_selector('opus_256')


def test_download_sync_reencode_writes_mp3_320_postprocessor(tmp_path, monkeypatch):
    seen = {}
    audio = tmp_path / 'Song.mp3'
    audio.write_bytes(b'x')

    class _FakeYDL:
        def __init__(self, opts):
            seen['pp'] = opts.get('postprocessors')

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    client._download_sync('https://youtu.be/abc', 'Song')
    assert seen['pp'] == [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '320',
    }]


def test_download_sync_extracts_fresh_after_probe(tmp_path, monkeypatch):
    """Probe URLs (itag 774 especially) 403 if downloaded later. Fetch extracts again."""
    audio = tmp_path / 'Song.opus'
    audio.write_bytes(b'x')
    seen = {'extract': 0, 'process': 0}

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            seen['extract'] += 1
            if download:
                return {'filepath': str(audio)}
            return {
                'id': 'jNQXAC9IVRw',
                'formats': [
                    {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
                ],
            }

        def process_ie_result(self, info, download=True):
            seen['process'] += 1
            raise RuntimeError('should not download from cached probe')

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    cand = _yt_track(filename='jNQXAC9IVRw||Me at the zoo')
    client.refresh_claimed_quality([cand])
    assert seen['extract'] == 1
    path = client._download_sync('https://www.youtube.com/watch?v=jNQXAC9IVRw', 'Me at the zoo')
    assert path == str(audio)
    assert seen['extract'] == 2
    assert seen['process'] == 0


def test_download_sync_extracts_fresh_when_probe_cache_expired(tmp_path, monkeypatch):
    audio = tmp_path / 'Song.opus'
    audio.write_bytes(b'x')
    seen = {'extract': 0}

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            seen['extract'] += 1
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    client._yt_probe_info = {
        'jNQXAC9IVRw': (0.0, {'id': 'jNQXAC9IVRw'}),  # expired
    }
    path = client._download_sync('https://www.youtube.com/watch?v=jNQXAC9IVRw', 'Song')
    assert path == str(audio)
    assert seen['extract'] == 1


def test_get_best_audio_format_picks_opus_160_rung():
    client = _bare_client()
    formats = [
        {'vcodec': 'avc1', 'acodec': 'mp4a.40.2', 'abr': 128},  # muxed, skip
        {'vcodec': 'none', 'acodec': 'opus', 'abr': 50},
        {'vcodec': 'none', 'acodec': 'opus', 'abr': 160},
        {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128},
        {'vcodec': 'none', 'acodec': 'none'},
    ]
    best = client._get_best_audio_format(formats, tier='opus_160')
    assert best['abr'] == 160


def test_get_best_audio_format_empty_and_video_only():
    client = _bare_client()
    assert client._get_best_audio_format([]) is None
    assert client._get_best_audio_format([{'vcodec': 'avc1', 'acodec': 'mp4a.40.2'}]) is None


def test_get_best_audio_format_opus_160_does_not_upgrade_to_aac_256():
    client = _bare_client()
    formats = [
        {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm'},
        {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 256, 'ext': 'm4a'},
    ]
    best = client._get_best_audio_format(formats, tier='opus_160')
    assert best['abr'] == 160
    assert 'opus' in best['acodec']


def test_get_best_audio_format_aac_128_does_not_upgrade_to_opus_256():
    client = _bare_client()
    formats = [
        {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm'},
        {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a'},
    ]
    best = client._get_best_audio_format(formats, tier='aac_128')
    assert best['abr'] == 128
    assert 'mp4a' in best['acodec']


def test_get_best_audio_format_walks_down_when_rung_missing():
    client = _bare_client()
    formats = [
        {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm'},
    ]
    best = client._get_best_audio_format(formats, tier='aac_256')
    assert best['abr'] == 160


def test_audio_format_selector_strings():
    assert youtube_audio_format_selector('opus_160').startswith('bestaudio[acodec^=opus]')
    assert 'm4a' in youtube_audio_format_selector('aac_128')
    assert 'abr>=192' in youtube_audio_format_selector('opus_256')
    assert youtube_audio_format_selector('nope') == 'bestaudio/best'


def test_youtube_track_result_strips_topic_suffix_and_parses_title():
    tr = _bare_client()._youtube_to_track_result({
        'id': 'vid',
        'title': 'Some Song',
        'duration': 200,
        'uploader': 'Radiohead - Topic',
    })
    assert tr.artist == 'Radiohead'
    assert tr.title == 'Some Song'
    assert tr.duration == 200_000
    assert tr.quality == 'opus'


def test_youtube_track_result_artist_dash_title():
    tr = _bare_client()._youtube_to_track_result({
        'id': 'vid',
        'title': 'NIN - Hurt',
        'duration': 90,
    })
    assert tr.artist == 'NIN'
    assert tr.title == 'Hurt'


# ── documentation pins ─────────────────────────────────────────────────────

def _slice_between(text, start, end):
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    assert i >= 0 and j > i, f'missing markers {start!r} / {end!r}'
    return text[i:j]


def test_docs_mention_youtube_reencode_default():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8')
    helper = (root / 'webui' / 'static' / 'helper.js').read_text(encoding='utf-8')
    docs = (root / 'webui' / 'static' / 'docs.js').read_text(encoding='utf-8')
    youtube_docs = _slice_between(docs, 'YouTube Configuration', 'UI Appearance')
    youtube_help = _slice_between(helper, "'#youtube-settings-container'", "'#quality-profile-section'")
    transcode_help = _slice_between(index, 'id="youtube-transcode"', 'id="youtube-transcode-options"')
    assert 'Re-encode YouTube audio' in youtube_docs
    assert 'MP3 320' in youtube_docs
    assert 'Re-encode to MP3 320 (on by default)' in youtube_help
    assert 'Premium audio' in youtube_help
    assert 'Netscape/Mozilla' in index and 'cookies.txt' in index
    assert 'Netscape' in docs and 'cookies.txt' in docs
    assert 'Premium audio qualities' in index
    assert 'Opus or AAC' in transcode_help
    assert 'quality list' in transcode_help
    assert 'minimum confidence threshold' not in youtube_docs.lower()
    search_yt = _slice_between(docs, 'Youtube cookies', 'Downloading Music')
    assert 'minimum confidence threshold' not in search_yt.lower()
    assert 'Premium audio' in search_yt or 'bot detection' in search_yt


def test_quality_profile_help_covers_youtube():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    helper = (root / 'webui' / 'static' / 'helper.js').read_text(encoding='utf-8')
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8')
    qp_help = _slice_between(helper, "'#quality-profile-section'", "'.preset-button'")
    transcode_help = _slice_between(index, 'id="youtube-transcode"', 'id="youtube-transcode-options"')
    assert 'YouTube' in qp_help
    assert 'Soulseek downloads' not in qp_help
    assert 'Opus or AAC' in transcode_help
    assert 'quality list' in transcode_help


def test_settings_ui_has_no_preferred_youtube_audio_control():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8')
    settings = (root / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    assert 'id="youtube-audio-format"' not in index
    assert 'Preferred YouTube audio' not in index
    assert 'youtube-audio-format' not in settings
    assert 'id="youtube-transcode" checked' in index
    assert 'id="youtube-transcode-options" style="display: none;"' not in index
    assert "settings.youtube?.transcode !== false" in settings
    assert "transcode_codec: document.getElementById('youtube-transcode-codec')?.value || 'mp3'" in settings
    assert "transcode_bitrate: document.getElementById('youtube-transcode-bitrate')?.value || '320'" in settings


def test_user_facing_youtube_quality_copy_is_not_internal():
    """YouTube re-encode copy must not mention itags, yt-dlp, probes, or the ladder."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    helper = (root / 'webui' / 'static' / 'helper.js').read_text(encoding='utf-8')
    docs = (root / 'webui' / 'static' / 'docs.js').read_text(encoding='utf-8')
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8')

    youtube_help = _slice_between(helper, "'#youtube-settings-container'", "'#quality-profile-section'")
    other_docs = _slice_between(docs, 'YouTube Configuration', 'UI Appearance')
    transcode_help = _slice_between(index, 'id="youtube-transcode"', 'id="youtube-transcode-options"')

    forbidden = ('itag', 'yt-dlp', 'bestaudio', 'extract_flat', 'ladder', 'probe', 'stamp')
    for chunk in (youtube_help, other_docs, transcode_help):
        lower = chunk.lower()
        for word in forbidden:
            assert word not in lower, f'{word!r} leaked into user-facing copy:\n{chunk}'


# ── realistic yt-dlp format lists (typical free / Premium itags) ───────────
#
# Shape matches extract_info() on a real watch page: muxed itag 18, video-only,
# storyboard (acodec/vcodec none), DASH audio 139/140/249/250/251. Premium
# extras 141/774 are appended only when the account actually has them.

_REAL_FREE_FORMATS = [
    {'format_id': 'sb0', 'vcodec': 'none', 'acodec': 'none', 'ext': 'mhtml'},
    {'format_id': '18', 'vcodec': 'avc1.42001E', 'acodec': 'mp4a.40.2', 'abr': 96, 'ext': 'mp4', 'tbr': 200},
    {'format_id': '137', 'vcodec': 'avc1.640028', 'acodec': 'none', 'ext': 'mp4', 'tbr': 2500},
    {'format_id': '139', 'vcodec': 'none', 'acodec': 'mp4a.40.5', 'abr': 48, 'ext': 'm4a', 'tbr': 49},
    {'format_id': '140', 'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'tbr': 129},
    {'format_id': '249', 'vcodec': 'none', 'acodec': 'opus', 'abr': 50, 'ext': 'webm', 'tbr': 52},
    {'format_id': '250', 'vcodec': 'none', 'acodec': 'opus', 'abr': 70, 'ext': 'webm', 'tbr': 73},
    {'format_id': '251', 'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'tbr': 165},
]
_REAL_PREMIUM_FORMATS = _REAL_FREE_FORMATS + [
    {'format_id': '141', 'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 256, 'ext': 'm4a', 'tbr': 257},
    {'format_id': '774', 'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'tbr': 260},
]


def test_realistic_free_formats_pick_opus_160_not_muxed():
    best = _bare_client()._get_best_audio_format(_REAL_FREE_FORMATS, tier='opus_256')
    assert best['format_id'] == '251'
    from core.quality.source_map import quality_from_youtube
    aq = quality_from_youtube(best)
    assert aq.format == 'opus'
    assert aq.bitrate == 160


def test_realistic_premium_opus_256_picks_itag_774():
    best = _bare_client()._get_best_audio_format(_REAL_PREMIUM_FORMATS, tier='opus_256')
    assert best['format_id'] == '774'
    assert best['abr'] == 256


def test_realistic_free_aac_128_picks_itag_140():
    best = _bare_client()._get_best_audio_format(_REAL_FREE_FORMATS, tier='aac_128')
    assert best['format_id'] == '140'
    from core.quality.source_map import quality_from_youtube
    assert quality_from_youtube(best).format == 'aac'
    assert quality_from_youtube(best).bitrate == 128


def test_realistic_premium_aac_256_picks_itag_141_not_opus_774():
    best = _bare_client()._get_best_audio_format(_REAL_PREMIUM_FORMATS, tier='aac_256')
    assert best['format_id'] == '141'
    assert best['abr'] == 256


def test_realistic_opus_160_does_not_fetch_premium_256():
    best = _bare_client()._get_best_audio_format(_REAL_PREMIUM_FORMATS, tier='opus_160')
    assert best['format_id'] == '251'


def test_realistic_he_aac_walks_to_opus_when_140_absent():
    """HE-AAC 48 is below the aac_128 rung; walk-down prefers Opus 160."""
    formats = [f for f in _REAL_FREE_FORMATS if f['format_id'] != '140']
    best = _bare_client()._get_best_audio_format(formats, tier='aac_128')
    assert best['format_id'] == '251'


# ── _get_best_audio_format / selector edge cases ───────────────────────────

def test_get_best_audio_format_none_and_missing_abr_uses_tbr():
    client = _bare_client()
    assert client._get_best_audio_format(None) is None
    best = client._get_best_audio_format(
        [{'vcodec': 'none', 'acodec': 'opus', 'abr': 0, 'tbr': 160, 'ext': 'webm'}],
        tier='opus_160',
    )
    assert best['tbr'] == 160


def test_get_best_audio_format_ext_only_opus_and_aac():
    client = _bare_client()
    opus = client._get_best_audio_format(
        [
            {'vcodec': 'none', 'acodec': '', 'ext': 'webm', 'abr': 160},
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'ext': 'm4a', 'abr': 128},
        ],
        tier='opus_160',
    )
    assert opus['abr'] == 160
    aac = client._get_best_audio_format(
        [
            {'vcodec': 'none', 'acodec': '', 'ext': 'm4a', 'abr': 128},
            {'vcodec': 'none', 'acodec': 'opus', 'ext': 'webm', 'abr': 160},
        ],
        tier='aac_128',
    )
    assert aac['abr'] == 128


def test_get_best_audio_format_vorbis_is_not_an_opus_rung():
    """Legacy Vorbis 171 is ogg, not an Opus ladder rung — walk to AAC 128."""
    client = _bare_client()
    best = client._get_best_audio_format(
        [
            {'vcodec': 'none', 'acodec': 'vorbis', 'abr': 128, 'ext': 'webm', 'format_id': '171'},
            {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'format_id': '140'},
        ],
        tier='opus_160',
    )
    assert best['format_id'] == '140'


def test_audio_format_selector_unknown_is_bestaudio():
    assert youtube_audio_format_selector('nope') == 'bestaudio/best'
    assert youtube_audio_format_selector('') == 'bestaudio/best'


# ── video id + refresh edge cases ──────────────────────────────────────────

@pytest.mark.parametrize("filename,want", [
    ('abc123||Song', 'abc123'),
    ('  vid  ||Song', 'vid'),
    ('vid||', 'vid'),
    ('vid||Song||extra', 'vid'),
    ('nofileseparator', ''),
    ('', ''),
    (None, ''),
    ('||Song', ''),
    ('   ||Song', ''),
])
def test_video_id_from_candidate(filename, want):
    cand = _yt_track(filename=filename) if filename is not None else _yt_track()
    if filename is None:
        cand.filename = None
    else:
        cand.filename = filename
    assert YouTubeClient._video_id_from_candidate(cand) == want


def test_refresh_skips_non_youtube_and_missing_id(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'},
        ],
    }, calls=calls)
    tidal = _yt_track(username='tidal', filename='tid||Song', bitrate=1411)
    bad = _yt_track(filename='nofileseparator', bitrate=160)
    _bare_client().refresh_claimed_quality([tidal, bad])
    assert calls == []
    assert tidal.bitrate == 1411
    assert bad.bitrate == 160


def test_refresh_limit_zero_and_none_candidates(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    calls = []
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm'},
        ],
    }, calls=calls)
    cand = _yt_track(bitrate=160)
    client = _bare_client()
    client.refresh_claimed_quality(None)
    client.refresh_claimed_quality([cand], limit=0)
    client.refresh_claimed_quality([cand], limit=-1)
    assert calls == []
    assert cand.bitrate == 160


def test_refresh_info_none_or_missing_formats_keeps_claim(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    cand = _yt_track(bitrate=160)
    _patch_ydl(monkeypatch, None)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.bitrate == 160
    _patch_ydl(monkeypatch, {})
    _bare_client().refresh_claimed_quality([cand])
    assert cand.bitrate == 160


def test_refresh_honours_opus_256_on_realistic_premium(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'opus_256')
    _patch_ydl(monkeypatch, {'formats': _REAL_PREMIUM_FORMATS})
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'opus'
    assert cand.bitrate == 256


def test_quality_filter_probe_exception_still_ranks_search_claim(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS], False)

    class _Boom:
        def refresh_claimed_quality(self, candidates, *, limit=3, **kwargs):
            raise RuntimeError('probe exploded')

    class _Orch:
        def client(self, name):
            return _Boom()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    cand = _yt_track(bitrate=160)
    result = get_valid_candidates([cand], _Expected(), 'Artist Song')
    assert result == [cand]


def test_quality_filter_preferred_aac_128_rejected_by_aac_256_floor(monkeypatch):
    _patch_profile(monkeypatch, [_AAC_256], False)

    class _Orch:
        def client(self, name):
            return _bare_client()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'aac_128')
    _patch_ydl(monkeypatch, {'formats': _REAL_FREE_FORMATS})
    cand = _yt_track(bitrate=160)
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == []
    assert cand.quality == 'aac'
    assert cand.bitrate == 128


# ── download_sync retries ──────────────────────────────────────────────────

def test_download_sync_aborts_on_shutdown(monkeypatch):
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = lambda: True
    assert client._download_sync('https://youtu.be/abc', 'Song') is None


def test_download_sync_third_retry_uses_muxed_best(tmp_path, monkeypatch):
    audio = tmp_path / 'Song.opus'
    audio.write_bytes(b'x')
    seen = []
    cookie_keys = []

    class _FakeYDL:
        def __init__(self, opts):
            seen.append(opts.get('format'))
            cookie_keys.append(('cookiefile' in opts, 'cookiesfrombrowser' in opts))
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            if len(seen) < 3:
                raise RuntimeError('temporary extractor error')
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    monkeypatch.setattr(
        'core.youtube_client.youtube_requested_tier',
        lambda: 'opus_160',
    )
    client = _bare_client()
    client.download_opts = {'quiet': True, 'cookiefile': '/tmp/cookies.txt'}
    client.shutdown_check = None
    result = client._download_sync('https://youtu.be/abc', 'Song')
    assert result == str(audio)
    assert len(seen) == 3
    assert 'opus' in seen[0]
    assert 'opus' in seen[1]  # retry 2 still prefers opus; keeps cookies
    assert seen[2] == 'best'
    assert cookie_keys[0] == (True, False)
    assert cookie_keys[1] == (True, False)
    assert cookie_keys[2] == (False, False)  # last ditch: expired cookies must not poison 'best'


def test_download_sync_retry_keeps_cookies_and_tries_web_music_only(tmp_path, monkeypatch):
    audio = tmp_path / 'Song.m4a'
    audio.write_bytes(b'x')
    cookie_keys = []
    extractor_clients = []
    check_formats = []

    class _FakeYDL:
        def __init__(self, opts):
            cookie_keys.append(('cookiefile' in opts, 'cookiesfrombrowser' in opts))
            extractor_clients.append(
                (opts.get('extractor_args') or {}).get('youtube', {}).get('player_client')
            )
            check_formats.append(opts.get('check_formats'))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            if len(cookie_keys) == 1:
                raise RuntimeError('restricted format')
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    client = _bare_client()
    client.download_opts = {'quiet': True, 'cookiefile': '/tmp/cookies.txt'}
    client.shutdown_check = None
    assert client._download_sync('https://youtu.be/abc', 'Song') == str(audio)
    assert cookie_keys[0] == (True, False)
    assert cookie_keys[1] == (True, False)
    assert extractor_clients[0] == ['web_music', 'default']
    assert extractor_clients[1] == ['web_music']
    assert check_formats == ['selected', 'selected']


# ── quality filter + download selector extra edges ─────────────────────────


def test_quality_filter_empty_profile_keeps_youtube_even_without_fallback(monkeypatch):
    _patch_profile(monkeypatch, [], False)
    cand = _yt_track()
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == [cand]


def test_quality_filter_without_orchestrator_ranks_search_claim(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS], False)
    monkeypatch.setattr(validation, 'download_orchestrator', None)
    cand = _yt_track(bitrate=160)
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == [cand]


def test_quality_filter_when_orchestrator_client_raises(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS], False)

    class _Orch:
        def client(self, name):
            raise RuntimeError('youtube client not ready')

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    cand = _yt_track(bitrate=160)
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == [cand]


def test_quality_filter_skips_probe_when_client_has_no_refresh(monkeypatch):
    _patch_profile(monkeypatch, [_OPUS], False)

    class _Orch:
        def client(self, name):
            return object()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    cand = _yt_track(bitrate=160)
    assert get_valid_candidates([cand], _Expected(), 'Artist Song') == [cand]


def test_download_sync_selector_follows_requested_tier(tmp_path, monkeypatch):
    audio = tmp_path / 'Song.m4a'
    audio.write_bytes(b'x')
    seen = []

    class _FakeYDL:
        def __init__(self, opts):
            seen.append(opts.get('format'))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {'filepath': str(audio)}

        def prepare_filename(self, info):
            return str(tmp_path / 'Song.webm')

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _FakeYDL)
    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: {'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'},
    )
    monkeypatch.setattr(
        'core.youtube_client.youtube_requested_tier',
        lambda: 'aac_128',
    )
    client = _bare_client()
    client.download_opts = {'quiet': True}
    client.shutdown_check = None
    assert client._download_sync('https://youtu.be/abc', 'Song') == str(audio)
    assert seen[0] == youtube_audio_format_selector('aac_128')
    assert 'm4a' in seen[0]


def test_default_youtube_config_has_no_preferred_audio_format():
    from core.settings import ConfigManager
    youtube = ConfigManager._get_default_config(None)['youtube']
    assert 'audio_format' not in youtube
    assert youtube['transcode'] is True
    assert youtube['transcode_codec'] == 'mp3'
    assert youtube['transcode_bitrate'] == '320'


def test_refresh_only_ultra_low_stamps_honest_bitrate_not_160(monkeypatch):
    monkeypatch.setattr(youtube_client, '_resolve_cookie_opts', lambda: {})
    _use_tier(monkeypatch, 'opus_256')
    _patch_ydl(monkeypatch, {
        'formats': [
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 50, 'ext': 'webm', 'format_id': '249'},
            {'vcodec': 'none', 'acodec': 'opus', 'abr': 70, 'ext': 'webm', 'format_id': '250'},
            {'vcodec': 'none', 'acodec': 'mp4a.40.5', 'abr': 48, 'ext': 'm4a', 'format_id': '139'},
        ],
    })
    cand = _yt_track(bitrate=160)
    _bare_client().refresh_claimed_quality([cand])
    assert cand.quality == 'opus'
    assert cand.bitrate == 70


def test_catalog_hit_claims_opus_160_not_mp3():
    tr = _bare_client()._ytmusic_hit_to_track_result({
        'id': 'vid1',
        'name': 'Song',
        'artists': ['Artist'],
        'album': 'Album',
        'duration_ms': 180_000,
    })
    assert tr.quality == 'opus'
    assert tr.bitrate == 160
    assert tr.audio_quality.format == 'opus'
    assert tr.filename == 'vid1||Song'
    assert tr.album == 'Album'


def test_catalog_hit_reencode_claims_mp3_320(monkeypatch):
    monkeypatch.setattr(
        youtube_client, '_youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    tr = _bare_client()._ytmusic_hit_to_track_result({
        'id': 'vid1',
        'name': 'Song',
        'artists': ['Artist'],
        'album': 'Album',
        'duration_ms': 180_000,
    })
    assert tr.quality == 'mp3'
    assert tr.bitrate == 320
    assert tr.audio_quality.format == 'mp3'
    assert tr.audio_quality.bitrate == 320

