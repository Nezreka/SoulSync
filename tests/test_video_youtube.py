"""Seam tests for core/video/youtube.py — the YouTube channel resolver.

Pure URL parsing + yt-dlp-dict → our-shape mapping are tested directly; the one
network call is exercised through an injected fake YoutubeDL, so nothing here
touches the network.
"""

import pytest

from core.video import youtube as yt


# ── parse_channel_url ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("https://www.youtube.com/@PlayStation", "https://www.youtube.com/@PlayStation/videos"),
    ("https://www.youtube.com/@PlayStation/videos", "https://www.youtube.com/@PlayStation/videos"),
    ("https://www.youtube.com/@PlayStation/streams", "https://www.youtube.com/@PlayStation/videos"),
    ("http://youtube.com/@GoodMythicalMorning", "https://www.youtube.com/@GoodMythicalMorning/videos"),
    ("youtube.com/@PlayStation", "https://www.youtube.com/@PlayStation/videos"),
    ("m.youtube.com/@PlayStation", "https://www.youtube.com/@PlayStation/videos"),
    ("https://www.youtube.com/channel/UCabc123", "https://www.youtube.com/channel/UCabc123/videos"),
    ("https://www.youtube.com/c/LinusTechTips", "https://www.youtube.com/c/LinusTechTips/videos"),
    ("https://www.youtube.com/user/PewDiePie", "https://www.youtube.com/user/PewDiePie/videos"),
    ("@PlayStation", "https://www.youtube.com/@PlayStation/videos"),
    ("PlayStation", "https://www.youtube.com/@PlayStation/videos"),  # bare → @handle
])
def test_parse_channel_url_accepts_channel_forms(raw, expected):
    assert yt.parse_channel_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    None,
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",   # a video, not a channel
    "https://www.youtube.com/playlist?list=PL123",   # a playlist
    "https://www.youtube.com/",                      # home
    "https://youtu.be/dQw4w9WgXcQ",                  # short video link
    "https://vimeo.com/@someone",                    # not youtube
    "https://www.youtube.com/shorts/abc123",         # a short, not a channel
])
def test_parse_channel_url_rejects_non_channels(raw):
    assert yt.parse_channel_url(raw) is None


# ── shape_channel ────────────────────────────────────────────────────────────

def _flat_info():
    return {
        "channel_id": "UCPlayStation",
        "channel": "PlayStation",
        "uploader": "PlayStation",
        "uploader_id": "@PlayStation",
        "channel_follower_count": 14_000_000,
        "playlist_count": 1200,
        "thumbnails": [
            {"url": "http://img/small.jpg", "width": 88, "height": 88},
            {"url": "http://img/avatar.jpg", "id": "avatar_uncropped", "width": 800, "height": 800},
            {"url": "http://img/banner.jpg", "id": "banner_uncropped", "width": 2048, "height": 1152},
        ],
        "entries": [
            {"id": "vid1", "title": "State of Play", "timestamp": 1_700_000_000,
             "duration": 3600, "view_count": 50000,
             "thumbnails": [{"url": "http://t/1.jpg", "width": 320, "height": 180}]},
            {"id": "vid2", "title": "Trailer", "upload_date": "20240115", "duration": 120,
             "thumbnail": "http://t/2.jpg"},
            {"id": "vid3", "title": "No date video"},  # sparse flat entry
            None,                                       # yt-dlp can yield Nones
            {"title": "missing id — skip"},             # no id → skipped
        ],
    }


def test_shape_channel_maps_channel_fields():
    out = yt.shape_channel(_flat_info())
    assert out["youtube_id"] == "UCPlayStation"
    assert out["title"] == "PlayStation"
    assert out["handle"] == "@PlayStation"
    assert out["avatar_url"] == "http://img/avatar.jpg"   # picked by id, not just size
    assert out["banner_url"] == "http://img/banner.jpg"   # banner separated from avatar
    assert out["subscriber_count"] == 14_000_000
    assert out["video_count"] == 1200                  # playlist_count, not len(videos)


def test_shape_channel_maps_and_filters_videos():
    out = yt.shape_channel(_flat_info())
    vids = out["videos"]
    # the None and the id-less entry are dropped
    assert [v["youtube_id"] for v in vids] == ["vid1", "vid2", "vid3"]
    # timestamp → ISO date
    assert vids[0]["published_at"] == "2023-11-14"
    assert vids[0]["duration_seconds"] == 3600
    assert vids[0]["thumbnail_url"] == "http://t/1.jpg"
    # upload_date 'YYYYMMDD' → ISO date; plain 'thumbnail' string honored
    assert vids[1]["published_at"] == "2024-01-15"
    assert vids[1]["thumbnail_url"] == "http://t/2.jpg"
    # sparse entry: no date, no crash
    assert vids[2]["published_at"] is None
    assert vids[2]["duration_seconds"] is None


def test_shape_channel_respects_limit():
    out = yt.shape_channel(_flat_info(), limit=1)
    assert len(out["videos"]) == 1
    assert out["videos"][0]["youtube_id"] == "vid1"


def test_shape_channel_uploader_id_not_a_handle_is_dropped():
    info = {"channel_id": "UCx", "channel": "X", "uploader_id": "UCx", "entries": []}
    assert yt.shape_channel(info)["handle"] is None


# ── resolve_channel (network call injected) ──────────────────────────────────

class _FakeYDL:
    """Stand-in for yt_dlp.YoutubeDL: records opts, returns a canned info dict."""
    last_opts = None
    last_url = None

    def __init__(self, opts):
        _FakeYDL.last_opts = opts
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        _FakeYDL.last_url = url
        assert download is False
        return _flat_info()


def test_resolve_channel_happy_path_uses_canonical_url_and_limit():
    out = yt.resolve_channel("https://www.youtube.com/@PlayStation", limit=2,
                             ydl_factory=_FakeYDL)
    assert out["youtube_id"] == "UCPlayStation"
    assert len(out["videos"]) == 2
    # resolver normalizes to the /videos uploads URL and passes the limit through
    assert _FakeYDL.last_url == "https://www.youtube.com/@PlayStation/videos"
    assert _FakeYDL.last_opts["playlistend"] == 2
    assert _FakeYDL.last_opts["extract_flat"] is True


def test_resolve_channel_rejects_non_channel_without_network():
    called = []

    def factory(opts):
        called.append(opts)
        raise AssertionError("should not be called for a non-channel URL")

    assert yt.resolve_channel("https://www.youtube.com/watch?v=abc", ydl_factory=factory) is None
    assert called == []


def test_resolve_channel_returns_none_on_extractor_error():
    class _Boom:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("DownloadError: channel not found")

    assert yt.resolve_channel("@nope", ydl_factory=_Boom) is None


def test_resolve_channel_none_when_info_has_no_channel_id():
    class _NoId:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"entries": [{"id": "v", "title": "t"}]}  # no channel_id/id

    assert yt.resolve_channel("@x", ydl_factory=_NoId) is None


# ── video_detail (full single-video metadata) ────────────────────────────────

def _video_info():
    return {
        "id": "vid1", "title": "State of Play", "description": "Everything announced.",
        "duration": 3725, "view_count": 1_250_000, "like_count": 42_000,
        "timestamp": 1_700_000_000, "channel": "PlayStation", "channel_id": "UCPlay",
        "webpage_url": "https://www.youtube.com/watch?v=vid1",
        "tags": ["ps5", "trailer"],
        "thumbnails": [{"url": "http://t/hi.jpg", "width": 1280, "height": 720}],
    }


class _FakeVideoYDL:
    last_url = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        _FakeVideoYDL.last_url = url
        assert download is False
        # full (non-flat) extraction: extract_flat must NOT be set
        assert "extract_flat" not in self.opts
        return _video_info()


def test_video_detail_pulls_full_metadata():
    out = yt.video_detail("vid1", ydl_factory=_FakeVideoYDL)
    assert out["youtube_id"] == "vid1"
    assert out["description"] == "Everything announced."
    assert out["duration_seconds"] == 3725
    assert out["view_count"] == 1_250_000 and out["like_count"] == 42_000
    assert out["published_at"] == "2023-11-14"
    assert out["channel_title"] == "PlayStation" and out["channel_id"] == "UCPlay"
    assert out["webpage_url"] == "https://www.youtube.com/watch?v=vid1"
    assert out["tags"] == ["ps5", "trailer"]
    # a raw id is turned into a watch URL
    assert _FakeVideoYDL.last_url == "https://www.youtube.com/watch?v=vid1"


def test_video_detail_accepts_watch_url_and_handles_failure():
    yt.video_detail("https://www.youtube.com/watch?v=abc", ydl_factory=_FakeVideoYDL)
    assert _FakeVideoYDL.last_url == "https://www.youtube.com/watch?v=abc"

    class _Boom(_FakeVideoYDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("unavailable")
    assert yt.video_detail("x", ydl_factory=_Boom) is None
    assert yt.video_detail("", ydl_factory=_FakeVideoYDL) is None
