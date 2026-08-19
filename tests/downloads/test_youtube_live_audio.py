"""Live YouTube format-probe and audio-download tests.

Gated behind ``-m youtube_live`` so default CI stays offline. Run locally:

    python -m pytest tests/downloads/test_youtube_live_audio.py -m youtube_live -v -s

Uses a short public video (the first YouTube upload, ~19s) — not copyrighted
music. Bot-checks, missing ffmpeg/deno, and network errors skip rather than
fail, matching the SoundCloud live pattern.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.download_plugins.types import TrackResult
from core.quality.source_map import quality_from_youtube
from core.youtube_client import (
    YouTubeClient,
    youtube_audio_postprocessor,
)

# First YouTube video: "Me at the zoo" — public, ~19 seconds.
_LIVE_VIDEO_ID = 'jNQXAC9IVRw'
_LIVE_URL = f'https://www.youtube.com/watch?v={_LIVE_VIDEO_ID}'

_BOT_HINTS = (
    'sign in to confirm',
    'confirm you’re not a bot',
    "confirm you're not a bot",
    'bot',
    'http error 429',
    'http error 403',
    'too many requests',
    'requested format is not available',
    'unable to extract',
    'video unavailable',
    'private video',
    'timed out',
    'name or service not known',
    'temporary failure',
    'network is unreachable',
    'connection reset',
    'ssl',
)


pytestmark = pytest.mark.youtube_live


def _skip_youtube(exc: BaseException) -> None:
    msg = f'{type(exc).__name__}: {exc}'.lower()
    if any(h in msg for h in _BOT_HINTS):
        pytest.skip(f'YouTube blocked or unavailable ({exc})')
    pytest.skip(f'YouTube live probe failed ({exc})')


def _bare_client(tmp_path: Path | None = None) -> YouTubeClient:
    client = YouTubeClient.__new__(YouTubeClient)
    client.shutdown_check = None
    if tmp_path is not None:
        client.download_path = tmp_path
        client.download_opts = {
            'outtmpl': str(tmp_path / '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'noplaylist': True,
        }
    return client


def _yt_track(video_id: str = _LIVE_VIDEO_ID) -> TrackResult:
    return TrackResult(
        username='youtube',
        filename=f'{video_id}||Me at the zoo',
        size=0,
        bitrate=160,
        duration=19_000,
        quality='opus',
        free_upload_slots=999,
        upload_speed=999,
        queue_length=0,
        artist='jawed',
        title='Me at the zoo',
    )


def _extract_or_skip():
    import yt_dlp

    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
    }
    try:
        from core.youtube_client import _resolve_cookie_opts
        opts.update(_resolve_cookie_opts())
    except Exception:
        pass
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(_LIVE_URL, download=False)
    except Exception as exc:  # noqa: BLE001 - live skip
        _skip_youtube(exc)
    if not info:
        pytest.skip('YouTube extract_info returned nothing')
    return info


def test_live_extract_lists_real_dash_audio_itags():
    """A real watch page must expose audio-only DASH (251 Opus and/or 140 AAC)."""
    info = _extract_or_skip()
    formats = info.get('formats') or []
    audio = [
        f for f in formats
        if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none')
    ]
    assert audio, f'no audio-only formats in {len(formats)} listed formats'
    ids = {str(f.get('format_id')) for f in audio}
    assert ids & {'251', '140', '250', '249', '139'}, f'unexpected audio itags: {ids}'

    client = _bare_client()
    best = client._get_best_audio_format(formats, tier='opus_256')
    assert best is not None
    aq = quality_from_youtube(best)
    assert aq.format in ('opus', 'aac')
    assert aq.bitrate and aq.bitrate >= 48
    assert aq.bit_depth is None
    assert not (aq.format == 'mp3' and aq.bitrate == 320)


def test_live_preferred_opus_and_aac_pick_matching_codecs():
    info = _extract_or_skip()
    formats = info.get('formats') or []
    client = _bare_client()
    opus = client._get_best_audio_format(formats, tier='opus_160')
    aac = client._get_best_audio_format(formats, tier='aac_128')
    assert opus is not None and aac is not None

    opus_q = quality_from_youtube(opus)
    aac_q = quality_from_youtube(aac)
    has_opus = any(
        'opus' in str(f.get('acodec') or '').lower()
        for f in formats
        if f.get('vcodec') == 'none'
    )
    has_aac = any(
        'mp4a' in str(f.get('acodec') or '').lower() or 'aac' in str(f.get('acodec') or '').lower()
        for f in formats
        if f.get('vcodec') == 'none'
    )
    if has_opus:
        assert opus_q.format == 'opus'
    if has_aac:
        assert aac_q.format == 'aac'
    if has_opus and has_aac:
        assert opus.get('format_id') != aac.get('format_id')


def test_live_refresh_stamps_claimed_quality_from_real_itags(monkeypatch):
    info = _extract_or_skip()

    class _YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            assert _LIVE_VIDEO_ID in url
            assert download is False
            return info

    monkeypatch.setattr('core.youtube_client.yt_dlp.YoutubeDL', _YDL)
    monkeypatch.setattr('core.youtube_client._resolve_cookie_opts', lambda: {})
    cand = _yt_track()
    _bare_client().refresh_claimed_quality([cand])
    assert cand.audio_quality.format in ('opus', 'aac')
    assert cand.bitrate >= 48
    assert cand.quality != 'mp3'


def test_live_download_remuxes_original_audio(tmp_path, monkeypatch):
    """Download the short public clip and keep the original Opus/AAC stream."""
    if not shutil.which('ffmpeg') and not shutil.which('ffmpeg.exe'):
        pytest.skip('ffmpeg not on PATH — remux requires it')
    _extract_or_skip()  # skip early on bot-check before spending a download

    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: youtube_audio_postprocessor(transcode=False),
    )
    monkeypatch.setattr(
        'core.youtube_client.youtube_requested_tier',
        lambda: 'opus_256',
    )
    monkeypatch.setattr('core.youtube_client._resolve_cookie_opts', lambda: {})

    client = _bare_client(tmp_path)
    try:
        path = client._download_sync(_LIVE_URL, 'Me at the zoo')
    except Exception as exc:  # noqa: BLE001 - live skip
        _skip_youtube(exc)

    if not path:
        pytest.skip('YouTube download returned no file (bot-check or format unavailable)')

    out = Path(path)
    assert out.is_file(), path
    assert out.stat().st_size > 5_000
    assert out.suffix.lower() in {'.opus', '.m4a', '.webm', '.ogg', '.aac'}
    assert out.suffix.lower() != '.mp3'


def test_live_download_preferred_opus_keeps_opus_when_available(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') and not shutil.which('ffmpeg.exe'):
        pytest.skip('ffmpeg not on PATH — remux requires it')
    info = _extract_or_skip()
    formats = info.get('formats') or []
    has_opus = any(
        f.get('vcodec') == 'none' and 'opus' in str(f.get('acodec') or '').lower()
        for f in formats
    )
    if not has_opus:
        pytest.skip('this video has no Opus DASH audio')

    monkeypatch.setattr(
        'core.youtube_client.youtube_audio_postprocessor_from_config',
        lambda: youtube_audio_postprocessor(transcode=False),
    )
    monkeypatch.setattr(
        'core.youtube_client.youtube_requested_tier',
        lambda: 'opus_160',
    )
    monkeypatch.setattr('core.youtube_client._resolve_cookie_opts', lambda: {})

    client = _bare_client(tmp_path)
    try:
        path = client._download_sync(_LIVE_URL, 'Me at the zoo')
    except Exception as exc:  # noqa: BLE001 - live skip
        _skip_youtube(exc)

    if not path:
        pytest.skip('YouTube download returned no file (bot-check or format unavailable)')

    out = Path(path)
    assert out.is_file()
    assert out.stat().st_size > 5_000
    assert out.suffix.lower() in {'.opus', '.webm'}
