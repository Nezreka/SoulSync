from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from t2tunes_probe import T2TunesClient, T2TunesError  # noqa: E402


class _Response:
    def __init__(self, payload=None, *, status_code=200, text="", headers=None, url="https://example.test/x"):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url
        self.ok = 200 <= status_code < 400

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"{self.status_code} error")


class T2TunesProbeTests(unittest.TestCase):
    def test_status_uses_api_status_endpoint(self):
        session = Mock()
        session.get.return_value = _Response({"amazonMusic": "up"})
        client = T2TunesClient("https://t2.example", session=session)

        self.assertTrue(client.amazon_music_is_up())
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], "https://t2.example/api/status")

    def test_search_flattens_nested_hits(self):
        session = Mock()
        session.get.return_value = _Response({
            "results": [{
                "hits": [{
                    "document": {
                        "__type": "Track",
                        "asin": "B001",
                        "title": "Song",
                        "artistName": "Artist",
                        "albumName": "Album",
                        "duration": 123,
                        "isrc": "USABC123",
                    }
                }]
            }]
        })
        client = T2TunesClient("https://t2.example", session=session)

        items = client.search("artist song")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].asin, "B001")
        self.assertTrue(items[0].is_track)
        self.assertEqual(items[0].duration_seconds, 123)
        self.assertEqual(session.get.call_args.kwargs["params"]["types"], "track,album")

    def test_media_handles_streamable_typo_and_decryption_flag(self):
        session = Mock()
        session.get.return_value = _Response([{
            "asin": "B001",
            "stremeable": True,
            "decryptionKey": "abc123",
            "tags": {
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "isrc": "USABC123",
            },
            "streamInfo": {
                "format": "flac",
                "codec": "flac",
                "sampleRate": 48000,
                "streamUrl": "https://cdn.example/song.flac",
            },
        }])
        client = T2TunesClient("https://t2.example", session=session)

        streams = client.media_from_asin("B001")

        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].streamable)
        self.assertTrue(streams[0].has_decryption_key)
        self.assertEqual(streams[0].stream_url, "https://cdn.example/song.flac")

    def test_non_json_response_raises_probe_error(self):
        session = Mock()
        session.get.return_value = _Response(ValueError("not json"), text="Service Unavailable")
        client = T2TunesClient("https://t2.example", session=session)

        with self.assertRaises(T2TunesError):
            client.status()

    def test_probe_stream_falls_back_to_range_get_when_head_is_blocked(self):
        session = Mock()
        session.head.return_value = _Response(status_code=405)
        session.get.return_value = _Response(
            status_code=206,
            headers={"content-type": "audio/flac", "content-length": "1"},
            url="https://cdn.example/song.flac",
        )
        client = T2TunesClient("https://t2.example", session=session)

        result = client.probe_stream("https://cdn.example/song.flac")

        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "GET range")
        self.assertEqual(result["content_type"], "audio/flac")
        self.assertEqual(session.get.call_args.kwargs["headers"], {"Range": "bytes=0-0"})


if __name__ == "__main__":
    unittest.main()
