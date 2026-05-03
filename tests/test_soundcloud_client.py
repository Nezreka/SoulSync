"""Unit + integration tests for ``core/soundcloud_client.py``.

The unit tests stub out ``yt_dlp`` so they run fast, deterministically,
and offline. They cover: search shape correctness, the artist/title
heuristic, the dispatch-key (``filename``) round trip, the download
state machine (success / failure / shutdown), the progress emitter, and
the cancel/clear ledger operations.

The integration tests are gated behind ``-m soundcloud_live`` so they
don't run in CI by default. Run them locally to verify against real
SoundCloud:

    python -m pytest tests/test_soundcloud_client.py -m soundcloud_live -v -s

They hit the public SoundCloud surface, so they require network access
and a working yt-dlp install.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import soundcloud_client
from core.soundcloud_client import SoundcloudClient, _sanitize_filename
from core.soulseek_client import AlbumResult, DownloadStatus, TrackResult


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_sanitize_filename_strips_reserved_chars() -> None:
    # Reserved chars become underscores; trailing punctuation gets stripped.
    assert _sanitize_filename('Track / Name : "Bad" ?') == 'Track _ Name _ _Bad'
    # Repeated underscores collapse, leading/trailing underscores trimmed.
    assert _sanitize_filename('////track////') == 'track'
    # Empty input still returns a usable filename, never an empty string.
    assert _sanitize_filename('') == 'soundcloud_track'


def test_split_artist_from_title_uses_dash_separator() -> None:
    artist, title = SoundcloudClient._split_artist_from_title(
        "Daft Punk - Get Lucky", "officialdaftpunk"
    )
    assert artist == "Daft Punk"
    assert title == "Get Lucky"


def test_split_artist_from_title_falls_back_to_uploader_when_no_dash() -> None:
    artist, title = SoundcloudClient._split_artist_from_title(
        "Some Mix Title", "uploader_handle"
    )
    assert artist == "uploader_handle"
    assert title == "Some Mix Title"


def test_split_artist_from_title_rejects_too_short_artist_part() -> None:
    """Things like "DJ - Mix" shouldn't get parsed as artist='DJ' / title='Mix'
    when a 2-char artist is plausibly noise — but our threshold is >=2, so
    "DJ" actually qualifies. This pins the boundary."""
    artist, title = SoundcloudClient._split_artist_from_title("a - hello", "uploader")
    # 'a' is 1 char → fall through to uploader
    assert artist == "uploader"
    assert title == "a - hello"


def test_split_artist_from_title_handles_empty_input() -> None:
    artist, title = SoundcloudClient._split_artist_from_title("", "fallback")
    assert artist == "fallback"
    assert title == ""


# ---------------------------------------------------------------------------
# Construction / availability gates
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dl(tmp_path: Path) -> Path:
    p = tmp_path / "downloads"
    p.mkdir()
    return p


def test_is_available_when_yt_dlp_installed(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    # In our test env yt-dlp is installed (it's a hard dep)
    assert client.is_available() is True
    assert client.is_configured() is True


def test_is_available_false_when_yt_dlp_missing(tmp_dl: Path, monkeypatch) -> None:
    monkeypatch.setattr(soundcloud_client, "yt_dlp", None)
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert client.is_available() is False
    assert client.is_configured() is False


def test_is_authenticated_always_false_until_oauth_ships(tmp_dl: Path) -> None:
    """Anonymous-only client. Pin the contract so a future OAuth tier
    has to explicitly flip this."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert client.is_authenticated() is False


def test_download_path_created_on_construction(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "downloads"
    assert not target.exists()
    SoundcloudClient(download_path=str(target))
    assert target.exists() and target.is_dir()


def test_set_shutdown_check_assigns_callable(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    sentinel = lambda: True  # noqa: E731
    client.set_shutdown_check(sentinel)
    assert client.shutdown_check is sentinel


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _run(coro):
    """Tiny helper — we have async methods to exercise but no async test runner."""
    return asyncio.run(coro)


def test_search_returns_empty_when_unavailable(tmp_dl: Path, monkeypatch) -> None:
    monkeypatch.setattr(soundcloud_client, "yt_dlp", None)
    client = SoundcloudClient(download_path=str(tmp_dl))
    tracks, albums = _run(client.search("anything"))
    assert tracks == []
    assert albums == []


def test_search_returns_empty_for_empty_or_invalid_query(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert _run(client.search("")) == ([], [])
    assert _run(client.search(None)) == ([], [])  # type: ignore[arg-type]
    assert _run(client.search(42)) == ([], [])  # type: ignore[arg-type]


def test_search_converts_yt_dlp_entries_to_track_results(tmp_dl: Path) -> None:
    """Happy-path search: yt-dlp returns a list of entries, the client
    converts each into a TrackResult, and the album list stays empty."""
    fake_entries = [
        {
            'id': '12345',
            'title': 'Daft Punk - Around the World',
            'uploader': 'daftpunkofficial',
            'url': 'https://soundcloud.com/daftpunk/around-the-world',
            'duration': 425.0,
        },
        {
            'id': '67890',
            'title': 'Some DJ Mix Set',
            'uploader': 'somedj',
            'url': 'https://soundcloud.com/somedj/some-mix',
            'duration': 3600.0,
        },
    ]
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_extract_search_entries', return_value=fake_entries):
        tracks, albums = _run(client.search("daft punk"))

    assert albums == []
    assert len(tracks) == 2

    # First entry: "Artist - Title" parsing kicked in
    t1 = tracks[0]
    assert isinstance(t1, TrackResult)
    assert t1.username == 'soundcloud'
    assert t1.artist == 'Daft Punk'
    assert t1.title == 'Around the World'
    assert t1.bitrate == 128
    assert t1.quality == 'mp3'
    assert t1.duration == 425000  # ms
    # Filename carries id + URL + display name for downstream dispatch
    parts = t1.filename.split('||')
    assert parts[0] == '12345'
    assert parts[1] == 'https://soundcloud.com/daftpunk/around-the-world'
    assert 'Daft Punk' in parts[2]
    # Source metadata roundtrips
    assert t1._source_metadata['source'] == 'soundcloud'
    assert t1._source_metadata['track_id'] == '12345'
    assert t1._source_metadata['permalink_url'] == 'https://soundcloud.com/daftpunk/around-the-world'

    # Second entry: no " - " in title, fall back to uploader as artist
    t2 = tracks[1]
    assert t2.artist == 'somedj'
    assert t2.title == 'Some DJ Mix Set'
    assert t2.duration == 3_600_000


def test_search_skips_entries_without_url(tmp_dl: Path) -> None:
    """No URL → can't download later → drop from results."""
    fake_entries = [
        {'id': '1', 'title': 'has url', 'url': 'https://soundcloud.com/x/y'},
        {'id': '2', 'title': 'no url'},  # gets skipped
        {'id': '', 'title': 'empty id', 'url': 'https://soundcloud.com/x/z'},  # also skipped
    ]
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_extract_search_entries', return_value=fake_entries):
        tracks, _ = _run(client.search("any"))
    assert len(tracks) == 1
    assert tracks[0]._source_metadata['track_id'] == '1'


def test_search_handles_yt_dlp_exception(tmp_dl: Path) -> None:
    """yt-dlp can raise on rate limit / network blip — caller still gets
    a clean empty list, never a raised exception."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_extract_search_entries',
                      side_effect=RuntimeError("network down")):
        tracks, albums = _run(client.search("anything"))
    assert tracks == []
    assert albums == []


def test_search_handles_empty_entries(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_extract_search_entries', return_value=[]):
        tracks, _ = _run(client.search("nothing"))
    assert tracks == []


def test_search_handles_malformed_entries_individually(tmp_dl: Path) -> None:
    """One bad entry shouldn't poison the entire result set."""
    fake_entries = [
        {'id': '1', 'title': 'good', 'url': 'https://x/1'},
        # Missing all required fields → conversion returns None → skipped
        {'something': 'weird'},
        {'id': '2', 'title': 'also good', 'url': 'https://x/2'},
    ]
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_extract_search_entries', return_value=fake_entries):
        tracks, _ = _run(client.search("any"))
    assert len(tracks) == 2


# ---------------------------------------------------------------------------
# Download orchestration
# ---------------------------------------------------------------------------


def test_download_rejects_invalid_filename_format(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    # No || separator
    assert _run(client.download('soundcloud', 'broken')) is None


def test_download_starts_thread_and_returns_id(tmp_dl: Path) -> None:
    """Verify the contract: returns a download_id, populates active_downloads,
    spawns a thread that ultimately drives state to terminal."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    completed_path = tmp_dl / "track.mp3"
    completed_path.write_bytes(b"x" * (200 * 1024))  # > MIN_AUDIO_SIZE

    with patch.object(client, '_download_sync', return_value=str(completed_path)):
        download_id = _run(client.download(
            'soundcloud',
            '999||https://soundcloud.com/x/y||Display Name',
            file_size=0,
        ))

    assert download_id is not None
    # Thread runs async; wait briefly for terminal state
    deadline = time.time() + 2
    while time.time() < deadline:
        with client._download_lock:
            state = client.active_downloads[download_id]['state']
        if state == 'Completed, Succeeded':
            break
        time.sleep(0.05)

    info = client.active_downloads[download_id]
    assert info['state'] == 'Completed, Succeeded'
    assert info['progress'] == 100.0
    assert info['file_path'] == str(completed_path)
    assert info['username'] == 'soundcloud'


def test_download_thread_marks_failed_when_sync_returns_none(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with patch.object(client, '_download_sync', return_value=None):
        download_id = _run(client.download(
            'soundcloud',
            '1||https://soundcloud.com/x/y||name',
        ))
    deadline = time.time() + 2
    while time.time() < deadline:
        with client._download_lock:
            state = client.active_downloads[download_id]['state']
        if state == 'Errored':
            break
        time.sleep(0.05)
    assert client.active_downloads[download_id]['state'] == 'Errored'


def test_download_thread_does_not_clobber_cancelled_state(tmp_dl: Path) -> None:
    """If a user cancels mid-download and the sync function then returns
    None, the thread should NOT overwrite the explicit Cancelled state
    with a generic Errored state."""
    client = SoundcloudClient(download_path=str(tmp_dl))

    def _slow_sync(download_id, *_):
        # Simulate cancellation racing a None return
        time.sleep(0.05)
        with client._download_lock:
            client.active_downloads[download_id]['state'] = 'Cancelled'
        return None

    with patch.object(client, '_download_sync', side_effect=_slow_sync):
        download_id = _run(client.download('soundcloud', '1||u||n'))

    deadline = time.time() + 2
    while time.time() < deadline:
        with client._download_lock:
            state = client.active_downloads[download_id]['state']
        if state == 'Cancelled':
            break
        time.sleep(0.05)
    assert client.active_downloads[download_id]['state'] == 'Cancelled'


# ---------------------------------------------------------------------------
# yt-dlp interaction (download_sync)
# ---------------------------------------------------------------------------


class _FakeYDL:
    """Minimal stand-in for yt_dlp.YoutubeDL used to exercise download_sync."""

    def __init__(self, opts):
        self.opts = opts
        self.last_url = None
        self.fake_info = {'id': 'abc', 'title': 'fake', 'ext': 'mp3'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        self.last_url = url
        if download:
            # Write a fake audio file to the resolved path
            resolved = self.prepare_filename(self.fake_info)
            Path(resolved).parent.mkdir(parents=True, exist_ok=True)
            Path(resolved).write_bytes(b"y" * (200 * 1024))
        return self.fake_info

    def prepare_filename(self, info):
        # Simulate yt-dlp's outtmpl substitution
        template = self.opts['outtmpl']
        return template.replace('%(ext)s', info.get('ext', 'mp3'))


def test_download_sync_writes_file_and_returns_path(tmp_dl: Path, monkeypatch) -> None:
    fake_yt_dlp = SimpleNamespace(
        YoutubeDL=_FakeYDL,
        utils=SimpleNamespace(DownloadError=Exception),
    )
    monkeypatch.setattr(soundcloud_client, "yt_dlp", fake_yt_dlp)
    client = SoundcloudClient(download_path=str(tmp_dl))

    with client._download_lock:
        client.active_downloads['dl1'] = {
            'id': 'dl1', 'filename': '', 'username': 'soundcloud',
            'state': 'Initializing', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
            'track_id': 'abc', 'permalink_url': 'u', 'display_name': 'My Track',
            'file_path': None,
        }

    result = client._download_sync('dl1', 'https://soundcloud.com/x/y', 'My Track')
    assert result is not None
    assert os.path.exists(result)
    assert os.path.getsize(result) > 100 * 1024


def test_download_sync_rejects_too_small_file(tmp_dl: Path, monkeypatch) -> None:
    """Files under MIN_AUDIO_SIZE_BYTES indicate yt-dlp got a preview
    snippet or junk response; reject and clean up."""

    class _TinyYDL(_FakeYDL):
        def __init__(self, opts):
            super().__init__(opts)
            self.fake_info = {'id': 'tiny', 'title': 'tiny', 'ext': 'mp3'}

        def extract_info(self, url, download=False):
            self.last_url = url
            if download:
                resolved = self.prepare_filename(self.fake_info)
                Path(resolved).parent.mkdir(parents=True, exist_ok=True)
                Path(resolved).write_bytes(b"y" * 500)  # Too small
            return self.fake_info

    fake_yt_dlp = SimpleNamespace(
        YoutubeDL=_TinyYDL,
        utils=SimpleNamespace(DownloadError=Exception),
    )
    monkeypatch.setattr(soundcloud_client, "yt_dlp", fake_yt_dlp)
    client = SoundcloudClient(download_path=str(tmp_dl))

    with client._download_lock:
        client.active_downloads['dl2'] = {
            'id': 'dl2', 'filename': '', 'username': 'soundcloud',
            'state': 'Initializing', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
            'track_id': 'tiny', 'permalink_url': 'u', 'display_name': 'Tiny',
            'file_path': None,
        }
    result = client._download_sync('dl2', 'https://soundcloud.com/x/y', 'Tiny')
    assert result is None
    # File got cleaned up after rejection
    target = tmp_dl / "Tiny.mp3"
    assert not target.exists()


def test_download_sync_handles_yt_dlp_raising(tmp_dl: Path, monkeypatch) -> None:
    """yt-dlp can raise DownloadError or any other exception. download_sync
    should surface a clean None instead of propagating."""
    class _BoomYDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, *a, **kw):
            raise RuntimeError("boom")
        def prepare_filename(self, info):
            return ""

    fake_yt_dlp = SimpleNamespace(
        YoutubeDL=_BoomYDL,
        utils=SimpleNamespace(DownloadError=Exception),
    )
    monkeypatch.setattr(soundcloud_client, "yt_dlp", fake_yt_dlp)
    client = SoundcloudClient(download_path=str(tmp_dl))

    with client._download_lock:
        client.active_downloads['dl3'] = {
            'id': 'dl3', 'filename': '', 'username': 'soundcloud',
            'state': 'Initializing', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
        }
    assert client._download_sync('dl3', 'https://soundcloud.com/x/y', 'Boom') is None


def test_download_sync_returns_none_when_yt_dlp_unavailable(tmp_dl: Path, monkeypatch) -> None:
    monkeypatch.setattr(soundcloud_client, "yt_dlp", None)
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert client._download_sync('any', 'u', 'name') is None


# ---------------------------------------------------------------------------
# Progress emitter
# ---------------------------------------------------------------------------


def test_update_download_progress_populates_ledger(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with client._download_lock:
        client.active_downloads['p1'] = {
            'id': 'p1', 'filename': '', 'username': 'soundcloud',
            'state': 'InProgress, Downloading', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
        }
    speed_start = time.time() - 1.0  # 1 second ago
    client._update_download_progress('p1', downloaded=512_000, total=1_024_000,
                                      speed_start=speed_start)
    info = client.active_downloads['p1']
    assert info['transferred'] == 512_000
    assert info['size'] == 1_024_000
    # 50% complete, capped below 100
    assert 49.0 <= info['progress'] <= 51.0
    # Speed roughly 512KB/s
    assert info['speed'] > 0
    # Time remaining should be roughly 1 second
    assert info['time_remaining'] is not None
    assert 0 < info['time_remaining'] < 5


def test_update_download_progress_caps_at_99_9(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with client._download_lock:
        client.active_downloads['p2'] = {
            'id': 'p2', 'filename': '', 'username': 'soundcloud',
            'state': 'InProgress, Downloading', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
        }
    client._update_download_progress('p2', downloaded=1_000_000,
                                      total=1_000_000, speed_start=time.time() - 1)
    assert client.active_downloads['p2']['progress'] == 99.9


def test_update_download_progress_silently_skips_unknown_id(tmp_dl: Path) -> None:
    """No-op if the download id isn't tracked — defensive against late hooks."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    # Should not raise
    client._update_download_progress('does_not_exist', 100, 1000, time.time())


# ---------------------------------------------------------------------------
# Status / cancel / clear
# ---------------------------------------------------------------------------


def test_get_all_downloads_returns_status_objects(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with client._download_lock:
        client.active_downloads['s1'] = {
            'id': 's1', 'filename': 'f', 'username': 'soundcloud',
            'state': 'InProgress, Downloading', 'progress': 33.3, 'size': 1000,
            'transferred': 333, 'speed': 100, 'time_remaining': 7,
            'file_path': None,
        }
    out = _run(client.get_all_downloads())
    assert len(out) == 1
    assert isinstance(out[0], DownloadStatus)
    assert out[0].id == 's1'
    assert out[0].progress == 33.3


def test_get_download_status_returns_none_for_unknown(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert _run(client.get_download_status('nope')) is None


def test_cancel_download_marks_state(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with client._download_lock:
        client.active_downloads['c1'] = {
            'id': 'c1', 'filename': '', 'username': 'soundcloud',
            'state': 'InProgress, Downloading', 'progress': 50.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
        }
    assert _run(client.cancel_download('c1')) is True
    assert client.active_downloads['c1']['state'] == 'Cancelled'


def test_cancel_download_with_remove_drops_entry(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    with client._download_lock:
        client.active_downloads['c2'] = {
            'id': 'c2', 'filename': '', 'username': 'soundcloud',
            'state': 'InProgress, Downloading', 'progress': 0.0, 'size': 0,
            'transferred': 0, 'speed': 0, 'time_remaining': None,
        }
    assert _run(client.cancel_download('c2', remove=True)) is True
    assert 'c2' not in client.active_downloads


def test_cancel_download_returns_false_for_unknown(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert _run(client.cancel_download('not_real')) is False


def test_clear_completed_drops_terminal_entries_only(tmp_dl: Path) -> None:
    """Terminal states get cleared; in-flight downloads survive."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    base = {'filename': '', 'username': 'soundcloud', 'progress': 0.0,
            'size': 0, 'transferred': 0, 'speed': 0, 'time_remaining': None}
    with client._download_lock:
        client.active_downloads['done'] = {**base, 'id': 'done', 'state': 'Completed, Succeeded'}
        client.active_downloads['err']  = {**base, 'id': 'err',  'state': 'Errored'}
        client.active_downloads['cnc']  = {**base, 'id': 'cnc',  'state': 'Cancelled'}
        client.active_downloads['live'] = {**base, 'id': 'live', 'state': 'InProgress, Downloading'}

    assert _run(client.clear_all_completed_downloads()) is True
    assert 'done' not in client.active_downloads
    assert 'err' not in client.active_downloads
    assert 'cnc' not in client.active_downloads
    assert 'live' in client.active_downloads


# ---------------------------------------------------------------------------
# Connection check
# ---------------------------------------------------------------------------


def test_check_connection_returns_false_when_unavailable(tmp_dl: Path, monkeypatch) -> None:
    monkeypatch.setattr(soundcloud_client, "yt_dlp", None)
    client = SoundcloudClient(download_path=str(tmp_dl))
    assert _run(client.check_connection()) is False


def test_check_connection_returns_true_on_successful_search(tmp_dl: Path) -> None:
    client = SoundcloudClient(download_path=str(tmp_dl))

    async def _fake_search(*_a, **_kw):
        return ([MagicMock()], [])

    with patch.object(client, 'search', side_effect=_fake_search):
        assert _run(client.check_connection()) is True


def test_check_connection_returns_false_when_search_raises(tmp_dl: Path) -> None:
    """Connection check shouldn't propagate the underlying exception."""
    client = SoundcloudClient(download_path=str(tmp_dl))

    async def _boom(*_a, **_kw):
        raise RuntimeError("network down")

    with patch.object(client, 'search', side_effect=_boom):
        assert _run(client.check_connection()) is False


# ---------------------------------------------------------------------------
# Live integration tests (gated)
# ---------------------------------------------------------------------------
# Run with: python -m pytest tests/test_soundcloud_client.py -m soundcloud_live -v -s
# These hit real SoundCloud — network required, slow, and skip in default CI.

pytestmark_live = pytest.mark.soundcloud_live


@pytestmark_live
def test_live_search_returns_real_results(tmp_dl: Path) -> None:
    """Real query against SoundCloud's public search."""
    client = SoundcloudClient(download_path=str(tmp_dl))
    tracks, albums = _run(client.search("daft punk around the world"))
    assert albums == []
    assert len(tracks) > 0
    # First result should at least have a title and a usable filename
    t = tracks[0]
    assert t.title or t.artist
    assert '||' in t.filename
    parts = t.filename.split('||')
    assert parts[0]  # track id
    assert parts[1].startswith('https://')


@pytestmark_live
def test_live_download_a_known_public_track(tmp_dl: Path) -> None:
    """Download a real public SoundCloud track end-to-end. This is the
    headline smoke test — if this passes, the client genuinely works.

    We use a SoundCloud-Provided promotional track to avoid hammering
    any specific creator's stats. If this URL ever 404s, swap it for
    another reliably-public free track.
    """
    client = SoundcloudClient(download_path=str(tmp_dl))
    # Search-then-download flow: pick the first hit for a popular query
    tracks, _ = _run(client.search("creative commons electronic music"))
    assert tracks, "Live search returned no results"
    first = tracks[0]
    download_id = _run(client.download(first.username, first.filename))
    assert download_id is not None

    # Wait up to 60s for completion
    deadline = time.time() + 60
    final_state = None
    final_path = None
    while time.time() < deadline:
        info = client.active_downloads.get(download_id, {})
        final_state = info.get('state')
        final_path = info.get('file_path')
        if final_state in {'Completed, Succeeded', 'Errored', 'Cancelled'}:
            break
        time.sleep(0.5)

    assert final_state == 'Completed, Succeeded', f"Live download didn't complete: {final_state}"
    assert final_path is not None
    assert os.path.exists(final_path)
    assert os.path.getsize(final_path) > 100 * 1024
