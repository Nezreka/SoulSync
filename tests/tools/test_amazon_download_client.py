"""Unit tests for core/amazon_download_client.py.

All network I/O and subprocess calls are mocked.
No real T2Tunes instance or ffmpeg binary required.

Run from project root:
    python -m pytest tests/tools/test_amazon_download_client.py -v
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.amazon_client import AmazonClientError, T2TunesStreamInfo
from core.amazon_download_client import (
    AmazonDownloadClient,
    MIN_AUDIO_BYTES,
    _codec_key,
    _file_extension,
    _quality_label,
)
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _stream_info(
    *,
    asin: str = "B09XYZ1234",
    streamable: bool = True,
    codec: str = "FLAC",
    sample_rate: int = 44100,
    stream_url: str = "https://cdn.example.com/track.enc.flac",
    decryption_key: Optional[str] = "deadbeef1234",
) -> T2TunesStreamInfo:
    return T2TunesStreamInfo(
        asin=asin,
        streamable=streamable,
        codec=codec,
        format=codec,
        sample_rate=sample_rate,
        stream_url=stream_url,
        decryption_key=decryption_key,
        title="Not Like Us",
        artist="Kendrick Lamar",
        album="GNX",
        isrc="USRC12345678",
    )


def _search_items(n_tracks: int = 2, n_albums: int = 1):
    from core.amazon_client import T2TunesSearchItem
    items = [
        T2TunesSearchItem(
            asin=f"B0TRACK{i}",
            title=f"Track {i}",
            artist_name="Kendrick Lamar",
            item_type="MusicTrack",
            album_name="GNX",
            album_asin="B0ALBUM1",
            duration_seconds=200 + i * 10,
            isrc=f"USRC{i:08d}",
        )
        for i in range(n_tracks)
    ]
    items += [
        T2TunesSearchItem(
            asin=f"B0ALBUM{j}",
            title=f"Album {j}",
            artist_name="Kendrick Lamar",
            item_type="MusicAlbum",
            album_name=f"Album {j}",
            album_asin=f"B0ALBUM{j}",
            duration_seconds=0,
        )
        for j in range(n_albums)
    ]
    return items


def _make_client(tmp_path: Path) -> AmazonDownloadClient:
    with patch("core.amazon_download_client.config_manager") as cfg:
        cfg.get.return_value = str(tmp_path)
        with patch("core.amazon_client.config_manager") as cfg2:
            cfg2.get.return_value = ""
            client = AmazonDownloadClient(download_path=str(tmp_path))
    return client


def _fake_chunked_response(data: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.headers = {"content-length": str(len(data))}

    chunk_size = 4096
    chunks = [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]

    def iter_content(chunk_size=None):
        yield from chunks

    resp.iter_content = iter_content
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Codec / quality helpers
# ---------------------------------------------------------------------------

class TestCodecHelpers:
    def test_codec_key_lowercases(self):
        assert _codec_key("FLAC") == "flac"
        assert _codec_key("OGG-Vorbis") == "ogg_vorbis"
        assert _codec_key("EAC3") == "eac3"

    def test_file_extension_known_codecs(self):
        assert _file_extension("FLAC") == "flac"
        assert _file_extension("ogg_vorbis") == "ogg"
        assert _file_extension("opus") == "opus"
        assert _file_extension("eac3") == "eac3"
        assert _file_extension("mp4") == "m4a"
        assert _file_extension("aac") == "m4a"
        assert _file_extension("mp3") == "mp3"

    def test_file_extension_unknown_falls_back(self):
        assert _file_extension("wtf_codec") == "bin"

    def test_quality_label_flac_lossless(self):
        assert _quality_label("flac", 44100) == "Lossless"
        assert _quality_label("FLAC", 48000) == "Lossless"

    def test_quality_label_flac_hires(self):
        assert _quality_label("flac", 96000) == "Hi-Res"
        assert _quality_label("flac", 192000) == "Hi-Res"

    def test_quality_label_lossy(self):
        assert _quality_label("opus") == "Lossy"
        assert _quality_label("eac3") == "Lossy"
        assert _quality_label("mp3") == "Lossy"


# ---------------------------------------------------------------------------
# is_configured / check_connection
# ---------------------------------------------------------------------------

class TestIsConfigured:
    def test_always_true(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.is_configured() is True


class TestCheckConnection:
    def test_true_when_client_authenticated(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.is_authenticated.return_value = True
        assert run(client.check_connection()) is True

    def test_false_when_client_down(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.is_authenticated.return_value = False
        assert run(client.check_connection()) is False

    def test_false_on_exception(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.is_authenticated.side_effect = Exception("timeout")
        assert run(client.check_connection()) is False


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_track_results(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.return_value = _search_items(n_tracks=2, n_albums=0)
        client._client.preferred_codec = "flac"

        tracks, albums = run(client.search("Kendrick Lamar"))

        assert len(tracks) == 2
        assert len(albums) == 0
        assert all(isinstance(t, TrackResult) for t in tracks)

    def test_track_fields(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.return_value = _search_items(n_tracks=1, n_albums=0)
        client._client.preferred_codec = "flac"

        tracks, _ = run(client.search("Not Like Us"))
        t = tracks[0]

        assert t.username == "amazon"
        assert "B0TRACK0" in t.filename
        assert "||" in t.filename
        assert t.artist == "Kendrick Lamar"
        assert t.title == "Track 0"
        assert t.album == "GNX"
        assert t.quality == "Lossless"
        assert t.duration == 200_000

    def test_track_source_metadata(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.return_value = _search_items(n_tracks=1, n_albums=0)
        client._client.preferred_codec = "flac"

        tracks, _ = run(client.search("test"))
        meta = tracks[0]._source_metadata

        assert meta["asin"] == "B0TRACK0"
        assert meta["album_asin"] == "B0ALBUM1"
        assert "isrc" in meta

    def test_returns_album_results(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.return_value = _search_items(n_tracks=0, n_albums=2)
        client._client.preferred_codec = "flac"

        _, albums = run(client.search("GNX"))

        assert len(albums) == 2
        assert all(isinstance(a, AlbumResult) for a in albums)

    def test_album_deduplication(self, tmp_path):
        from core.amazon_client import T2TunesSearchItem
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.preferred_codec = "flac"
        # Two hits with same album_asin
        dup = T2TunesSearchItem(
            asin="B0ALBUM0",
            title="GNX",
            artist_name="Kendrick Lamar",
            item_type="MusicAlbum",
            album_asin="B0ALBUM0",
        )
        client._client.search_raw.return_value = [dup, dup]

        _, albums = run(client.search("GNX"))
        assert len(albums) == 1

    def test_returns_empty_on_error(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.side_effect = AmazonClientError("fail")

        tracks, albums = run(client.search("anything"))
        assert tracks == []
        assert albums == []

    def test_soulseek_compat_fields(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.search_raw.return_value = _search_items(n_tracks=1, n_albums=0)
        client._client.preferred_codec = "flac"

        tracks, _ = run(client.search("test"))
        t = tracks[0]

        assert t.free_upload_slots == 999
        assert t.upload_speed == 999_999
        assert t.queue_length == 0
        assert t.size == 0


# ---------------------------------------------------------------------------
# _unique_path
# ---------------------------------------------------------------------------

class TestUniquePath:
    def test_returns_original_when_no_conflict(self, tmp_path):
        p = tmp_path / "track.flac"
        result = AmazonDownloadClient._unique_path(p)
        assert result == p

    def test_appends_counter_on_conflict(self, tmp_path):
        p = tmp_path / "track.flac"
        p.touch()
        result = AmazonDownloadClient._unique_path(p)
        assert result != p
        assert "(1)" in result.name

    def test_increments_counter(self, tmp_path):
        p = tmp_path / "track.flac"
        p.touch()
        (tmp_path / "track (1).flac").touch()
        result = AmazonDownloadClient._unique_path(p)
        assert "(2)" in result.name


# ---------------------------------------------------------------------------
# _record_to_status
# ---------------------------------------------------------------------------

class TestRecordToStatus:
    def test_fields_mapped(self):
        rec = {
            "filename": "B1||Artist - Title",
            "state": "downloading",
            "progress": 0.5,
            "size": 10_000_000,
            "transferred": 5_000_000,
            "speed": 1_000_000,
            "time_remaining": 5,
            "file_path": "/tmp/track.flac",
        }
        status = AmazonDownloadClient._record_to_status("dl-001", rec)

        assert status.id == "dl-001"
        assert status.filename == "B1||Artist - Title"
        assert status.username == "amazon"
        assert status.state == "downloading"
        assert status.progress == 0.5
        assert status.size == 10_000_000
        assert status.transferred == 5_000_000
        assert status.speed == 1_000_000
        assert status.time_remaining == 5
        assert status.file_path == "/tmp/track.flac"

    def test_defaults_for_missing_fields(self):
        status = AmazonDownloadClient._record_to_status("dl-002", {})
        assert status.state == "queued"
        assert status.progress == 0.0
        assert status.size == 0
        assert status.transferred == 0
        assert status.speed == 0
        assert status.time_remaining is None
        assert status.file_path is None


# ---------------------------------------------------------------------------
# _stream_to_file
# ---------------------------------------------------------------------------

class TestStreamToFile:
    def test_writes_file_and_returns_size(self, tmp_path):
        client = _make_client(tmp_path)
        data = b"X" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(data)

        out = tmp_path / "output.flac"
        downloaded = client._stream_to_file("https://example.com/t.flac", out, "dl-001")

        assert downloaded == len(data)
        assert out.exists()
        assert out.read_bytes() == data

    def test_raises_on_http_error(self, tmp_path):
        from requests import HTTPError
        client = _make_client(tmp_path)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(b"", status_code=403)

        out = tmp_path / "output.flac"
        with pytest.raises(HTTPError):
            client._stream_to_file("https://example.com/t.flac", out, "dl-001")

    def test_respects_shutdown_check(self, tmp_path):
        client = _make_client(tmp_path)
        data = b"X" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(data)
        client.shutdown_check = lambda: True  # trigger immediately

        out = tmp_path / "output.flac"
        with pytest.raises(RuntimeError, match="Shutdown"):
            client._stream_to_file("https://example.com/t.flac", out, "dl-001")
        assert not out.exists()

    def test_updates_engine_progress(self, tmp_path):
        import itertools
        client = _make_client(tmp_path)
        data = b"X" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(data)
        engine = MagicMock()
        client._engine = engine

        out = tmp_path / "output.flac"
        counter = itertools.count(0.0, 1.0)
        with patch("core.amazon_download_client.time") as mock_time:
            mock_time.monotonic.side_effect = lambda: next(counter)
            client._stream_to_file("https://example.com/t.flac", out, "dl-001")

        assert engine.update_record.called


# ---------------------------------------------------------------------------
# _decrypt_with_ffmpeg
# ---------------------------------------------------------------------------

class TestDecryptWithFfmpeg:
    def test_calls_ffmpeg_with_key(self, tmp_path):
        client = _make_client(tmp_path)
        enc = tmp_path / "track.enc.flac"
        enc.write_bytes(b"encrypted")
        out = tmp_path / "track.flac"

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr=b"")
                client._decrypt_with_ffmpeg(enc, out, "deadbeef1234")

        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert "-decryption_key" in cmd
        assert "deadbeef1234" in cmd
        assert str(enc) in cmd
        assert str(out) in cmd

    def test_raises_on_ffmpeg_failure(self, tmp_path):
        client = _make_client(tmp_path)
        enc = tmp_path / "track.enc.flac"
        enc.write_bytes(b"bad")
        out = tmp_path / "track.flac"

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stderr=b"Invalid data found"
                )
                with pytest.raises(RuntimeError, match="FFmpeg decryption failed"):
                    client._decrypt_with_ffmpeg(enc, out, "deadbeef1234")

    def test_raises_when_ffmpeg_missing(self, tmp_path):
        client = _make_client(tmp_path)
        enc = tmp_path / "track.enc.flac"
        out = tmp_path / "track.flac"

        with patch("shutil.which", return_value=None):
            # Ensure tools/ffmpeg.exe also absent
            with pytest.raises(RuntimeError, match="ffmpeg is required"):
                client._decrypt_with_ffmpeg(enc, out, "deadbeef1234")

    def test_uses_tools_ffmpeg_when_not_on_path(self, tmp_path):
        client = _make_client(tmp_path)
        enc = tmp_path / "track.enc.flac"
        enc.write_bytes(b"enc")
        out = tmp_path / "track.flac"

        fake_ffmpeg = tmp_path / "ffmpeg.exe"
        fake_ffmpeg.touch()

        with patch("shutil.which", return_value=None):
            with patch(
                "core.amazon_download_client.Path.__file__",
                create=True,
            ):
                import os as _os
                is_nt = _os.name == "nt"
                ffmpeg_name = "ffmpeg.exe" if is_nt else "ffmpeg"
                tools_dir = ROOT / "tools"
                tools_ffmpeg = tools_dir / ffmpeg_name
                if tools_ffmpeg.exists():
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
                        client._decrypt_with_ffmpeg(enc, out, "aabbcc")
                    assert str(tools_ffmpeg) in mock_run.call_args[0][0][0]


# ---------------------------------------------------------------------------
# _download_sync — integration of stream + decrypt
# ---------------------------------------------------------------------------

class TestDownloadSync:
    def _setup(self, tmp_path: Path, decryption_key: Optional[str] = "deadbeef"):
        client = _make_client(tmp_path)
        stream = _stream_info(decryption_key=decryption_key)
        client._client = MagicMock()
        client._client.media_from_asin.return_value = [stream]
        client._client.preferred_codec = "flac"

        audio_data = b"A" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(audio_data)
        return client, audio_data

    def test_returns_output_path_on_success(self, tmp_path):
        client, audio_data = self._setup(tmp_path)

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr=b"")
                # simulate ffmpeg writing output file
                def _ffmpeg_side_effect(cmd, capture_output=False):
                    # Write dummy decrypted data
                    out_path = Path(cmd[-1])
                    out_path.write_bytes(audio_data)
                    return MagicMock(returncode=0, stderr=b"")

                mock_run.side_effect = _ffmpeg_side_effect
                result = client._download_sync("dl-001", "B09XYZ1234", "Kendrick Lamar - Not Like Us")

        assert result is not None
        assert Path(result).exists()
        assert Path(result).suffix == ".flac"

    def test_tries_next_codec_when_stream_unavailable(self, tmp_path):
        client = _make_client(tmp_path)
        # FLAC: no streamable results; Opus: success
        flac_stream = _stream_info(streamable=False, codec="FLAC")
        opus_stream = _stream_info(streamable=True, codec="OPUS",
                                   stream_url="https://cdn.example.com/t.opus",
                                   decryption_key=None)
        client._client = MagicMock()
        client._client.preferred_codec = "flac"

        def _media(asin, codec):
            if codec == "flac":
                return [flac_stream]
            if codec == "opus":
                return [opus_stream]
            return []

        client._client.media_from_asin.side_effect = _media

        audio_data = b"B" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(audio_data)

        result = client._download_sync("dl-001", "B09XYZ1234", "Kendrick - Track")

        assert result is not None
        assert ".opus" in result

    def test_returns_none_when_file_too_small(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.preferred_codec = "flac"
        client._client.media_from_asin.return_value = [
            _stream_info(decryption_key=None)
        ]
        tiny_data = b"X" * 100  # way below MIN_AUDIO_BYTES
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(tiny_data)

        result = client._download_sync("dl-001", "B09XYZ1234", "Artist - Title")
        assert result is None

    def test_tries_next_codec_when_media_fails(self, tmp_path):
        client = _make_client(tmp_path)
        client._client = MagicMock()
        client._client.preferred_codec = "flac"

        call_count = {"n": 0}

        def _media(asin, codec):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise AmazonClientError("quota exceeded")
            stream = _stream_info(codec="OPUS", decryption_key=None)
            return [stream]

        client._client.media_from_asin.side_effect = _media

        audio_data = b"C" * (MIN_AUDIO_BYTES + 1024)
        client.session = MagicMock()
        client.session.get.return_value = _fake_chunked_response(audio_data)

        result = client._download_sync("dl-001", "B09XYZ1234", "Artist - Track")
        assert result is not None

    def test_decryption_failure_tries_next_codec(self, tmp_path):
        client, audio_data = self._setup(tmp_path, decryption_key="badkey")

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            with patch("subprocess.run") as mock_run:
                # First codec (flac) decryption fails; no more codecs succeed
                mock_run.return_value = MagicMock(returncode=1, stderr=b"decrypt error")
                result = client._download_sync("dl-001", "B09XYZ1234", "Artist - Track")

        # All codecs should fail since we return the same bad result for all
        assert result is None

    def test_updates_engine_state(self, tmp_path):
        client, audio_data = self._setup(tmp_path, decryption_key=None)
        engine = MagicMock()
        client._engine = engine

        result = client._download_sync("dl-001", "B09XYZ1234", "Artist - Track")

        update_calls = engine.update_record.call_args_list
        states = [c[0][2].get("state") for c in update_calls if "state" in c[0][2]]
        assert "downloading" in states

    def test_clear_stream_skips_ffmpeg(self, tmp_path):
        client, audio_data = self._setup(tmp_path, decryption_key=None)

        with patch("subprocess.run") as mock_run:
            result = client._download_sync("dl-001", "B09XYZ1234", "Artist - Track")

        mock_run.assert_not_called()
        assert result is not None

    def test_safe_filename_sanitisation(self, tmp_path):
        client, audio_data = self._setup(tmp_path, decryption_key=None)
        result = client._download_sync(
            "dl-001", "B09XYZ1234", "Björk / Sigur Rós: Hvarf<>Heim"
        )
        assert result is not None
        # Path must not contain illegal filesystem chars
        path = Path(result)
        assert "/" not in path.name
        assert "<" not in path.name
        assert ">" not in path.name


# ---------------------------------------------------------------------------
# download() — async dispatch
# ---------------------------------------------------------------------------

class TestDownloadDispatch:
    def test_raises_without_engine(self, tmp_path):
        client = _make_client(tmp_path)
        with pytest.raises(RuntimeError, match="_engine"):
            run(client.download("amazon", "B09XYZ1234||Artist - Title"))

    def test_returns_none_on_bad_filename(self, tmp_path):
        client = _make_client(tmp_path)
        client._engine = MagicMock()
        result = run(client.download("amazon", "no-pipe-delimiter-here"))
        assert result is None

    def test_dispatches_to_engine_worker(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.worker.dispatch.return_value = "dl-abc123"
        client._engine = engine

        result = run(client.download("amazon", "B09XYZ1234||Kendrick Lamar - Not Like Us"))

        assert result == "dl-abc123"
        dispatch_call = engine.worker.dispatch.call_args
        assert dispatch_call[1]["source_name"] == "amazon"
        assert dispatch_call[1]["target_id"] == "B09XYZ1234"
        assert dispatch_call[1]["display_name"] == "Kendrick Lamar - Not Like Us"
        assert dispatch_call[1]["impl_callable"] == client._download_sync

    def test_strips_whitespace_from_asin_and_name(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.worker.dispatch.return_value = "dl-xyz"
        client._engine = engine

        run(client.download("amazon", " B09XYZ1234 || Artist - Title "))

        call = engine.worker.dispatch.call_args
        assert call[1]["target_id"] == "B09XYZ1234"
        assert call[1]["display_name"] == "Artist - Title"

    def test_set_engine_wires_engine(self, tmp_path):
        client = _make_client(tmp_path)
        assert client._engine is None
        engine = MagicMock()
        client.set_engine(engine)
        assert client._engine is engine

    def test_set_shutdown_check_wires_callback(self, tmp_path):
        client = _make_client(tmp_path)
        assert client.shutdown_check is None
        check = lambda: False
        client.set_shutdown_check(check)
        assert client.shutdown_check is check

    def test_set_engine_allows_download_dispatch(self, tmp_path):
        """set_engine() must unblock download() — the live failure mode."""
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.worker.dispatch.return_value = "dl-wired"
        client.set_engine(engine)
        result = run(client.download("amazon", "B09XYZ1234||Artist - Title"))
        assert result == "dl-wired"


# ---------------------------------------------------------------------------
# Status interface
# ---------------------------------------------------------------------------

class TestStatusInterface:
    def test_get_all_downloads_empty_without_engine(self, tmp_path):
        client = _make_client(tmp_path)
        result = run(client.get_all_downloads())
        assert result == []

    def test_get_all_downloads_converts_records(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.get_all_records.return_value = {
            "dl-001": {
                "filename": "B1||A - T",
                "state": "complete",
                "progress": 1.0,
                "size": 5_000_000,
                "transferred": 5_000_000,
                "speed": 0,
            }
        }
        client._engine = engine

        statuses = run(client.get_all_downloads())
        assert len(statuses) == 1
        assert statuses[0].id == "dl-001"
        assert statuses[0].state == "complete"

    def test_get_download_status_returns_none_without_engine(self, tmp_path):
        client = _make_client(tmp_path)
        assert run(client.get_download_status("dl-001")) is None

    def test_get_download_status_hit(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.get_record.return_value = {
            "original_filename": "B1||A - T",
            "state": "downloading",
            "progress": 0.7,
            "size": 10_000_000,
            "transferred": 7_000_000,
            "speed": 500_000,
        }
        client._engine = engine

        status = run(client.get_download_status("dl-001"))
        assert status is not None
        assert status.state == "downloading"
        assert status.progress == 0.7
        engine.get_record.assert_called_once_with("amazon", "dl-001")

    def test_get_download_status_miss(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.get_record.return_value = None
        client._engine = engine

        assert run(client.get_download_status("nonexistent")) is None

    def test_cancel_returns_false_without_engine(self, tmp_path):
        client = _make_client(tmp_path)
        assert run(client.cancel_download("dl-001")) is False

    def test_cancel_delegates_to_engine(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.cancel_record.return_value = True
        client._engine = engine

        result = run(client.cancel_download("dl-001", remove=True))
        assert result is True
        engine.cancel_record.assert_called_once_with("amazon", "dl-001", remove=True)

    def test_clear_completed_returns_false_without_engine(self, tmp_path):
        client = _make_client(tmp_path)
        assert run(client.clear_all_completed_downloads()) is False

    def test_clear_completed_delegates_to_engine(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        client._engine = engine

        result = run(client.clear_all_completed_downloads())
        assert result is True
        engine.clear_completed.assert_called_once_with("amazon")

    def test_status_methods_return_gracefully_on_engine_error(self, tmp_path):
        client = _make_client(tmp_path)
        engine = MagicMock()
        engine.get_all_records.side_effect = Exception("engine crash")
        engine.get_record.side_effect = Exception("engine crash")
        engine.cancel_record.side_effect = Exception("engine crash")
        engine.clear_completed.side_effect = Exception("engine crash")
        client._engine = engine

        assert run(client.get_all_downloads()) == []
        assert run(client.get_download_status("dl-001")) is None
        assert run(client.cancel_download("dl-001")) is False
        assert run(client.clear_all_completed_downloads()) is False
