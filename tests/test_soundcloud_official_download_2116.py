"""#2116: with a free account's oauth token, soundcloud downloads take the
uploader's original file when the api says it is downloadable, and fall
back to the stream otherwise. yt-dlp is stubbed, nothing hits the network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import soundcloud_client
from core.soundcloud_client import SoundcloudClient


@pytest.fixture
def token(monkeypatch):
    values = {}
    real = soundcloud_client.config_manager.get
    monkeypatch.setattr(soundcloud_client.config_manager, 'get',
                        lambda key, default=None: values[key] if key in values else real(key, default))
    return values


def _fake_ydl(calls, fail_first=False):
    class _YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            calls.append(dict(self.opts))
            if fail_first and len(calls) == 1:
                raise RuntimeError('original download link expired')
            info = {'id': '1', 'title': 't', 'ext': 'wav'}
            if download:
                path = Path(self.prepare_filename(info))
                path.write_bytes(b'x' * (200 * 1024))
            return info

        def prepare_filename(self, info):
            return self.opts['outtmpl'].replace('%(ext)s', info['ext'])

    return SimpleNamespace(YoutubeDL=_YDL, utils=SimpleNamespace(DownloadError=Exception))


def test_a_token_logs_yt_dlp_in(tmp_path, monkeypatch, token):
    """logged in, yt-dlp offers the original as format "download" and
    prefers it; the token goes in the way its soundcloud extractor takes it."""
    calls = []
    monkeypatch.setattr(soundcloud_client, 'yt_dlp', _fake_ydl(calls))
    token['soundcloud_download.oauth_token'] = 'OAuth 2-abc'
    client = SoundcloudClient(download_path=str(tmp_path))
    assert client.is_authenticated() is True
    assert client._download_sync('d1', 'https://soundcloud.com/a/b', 'A - B')
    assert calls[0]['username'] == 'oauth' and calls[0]['password'] == '2-abc'


def test_without_a_token_it_stays_anonymous(tmp_path, monkeypatch, token):
    calls = []
    monkeypatch.setattr(soundcloud_client, 'yt_dlp', _fake_ydl(calls))
    token['soundcloud_download.oauth_token'] = ''
    client = SoundcloudClient(download_path=str(tmp_path))
    assert client.is_authenticated() is False
    assert client._download_sync('d1', 'https://soundcloud.com/a/b', 'A - B')
    assert 'password' not in calls[0]


def test_a_failed_original_falls_back_to_the_stream(tmp_path, monkeypatch, token):
    calls = []
    monkeypatch.setattr(soundcloud_client, 'yt_dlp', _fake_ydl(calls, fail_first=True))
    token['soundcloud_download.oauth_token'] = '2-abc'
    client = SoundcloudClient(download_path=str(tmp_path))
    assert client._download_sync('d1', 'https://soundcloud.com/a/b', 'A - B')
    assert len(calls) == 2
    assert 'format_id!=download' in calls[1]['format']


def test_the_candidate_says_which_it_will_be(tmp_path, monkeypatch):
    """an api flagged original is labelled as one; "FREE DL" in a title is not."""
    monkeypatch.setattr(soundcloud_client, 'yt_dlp', _fake_ydl([]))
    client = SoundcloudClient(download_path=str(tmp_path))
    base = {'id': '9', 'url': 'https://soundcloud.com/a/b', 'uploader': 'dj', 'duration': 200}
    official = client._sc_to_track_result({
        **base, 'title': 'A - B',
        'formats': [{'format_id': 'download', 'ext': 'wav', 'filesize': 5000},
                    {'format_id': 'hls_mp3_128', 'ext': 'mp3'}]})
    assert official.quality == 'wav' and official.bitrate is None
    assert official._source_metadata['official_download'] is True
    assert official._source_metadata['quality_label'] == 'Official free download (wav)'

    stream = client._sc_to_track_result({**base, 'title': 'A - B [FREE DL]',
                                         'formats': [{'format_id': 'hls_mp3_128', 'ext': 'mp3'}]})
    assert stream.quality == 'mp3' and stream.bitrate == 128
    assert stream._source_metadata['quality_label'] == 'Stream (~128 kbps)'
