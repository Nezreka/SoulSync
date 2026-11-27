"""a pasted youtube link on the files search (#2322): only youtube links
count, yt-dlp only ever sees a url rebuilt from the id, a video comes back as
one downloadable row and a playlist as its videos, capped."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core import youtube_client
from core.search import youtube_link
from core.search.youtube_link import (
    PLAYLIST_CAP,
    canonical_url,
    parse_youtube_url,
    resolve_youtube_link,
)
from core.youtube_client import YouTubeClient

VID = "dQw4w9WgXcQ"


@pytest.fixture(autouse=True)
def _reencode_off(monkeypatch):
    monkeypatch.setattr(youtube_client, "_youtube_transcode_settings",
                        lambda: (False, "mp3", "320"))


def _client():
    """the two YouTubeClient methods the rows use, no config behind them."""
    fake = SimpleNamespace()
    fake._youtube_to_track_result = lambda entry, best=None: YouTubeClient._youtube_to_track_result(
        fake, entry, best)
    fake._get_best_audio_format = lambda formats: YouTubeClient._get_best_audio_format(fake, formats)
    return fake


# ── url detection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    f"https://www.youtube.com/watch?v={VID}",
    f"http://youtube.com/watch?v={VID}&t=42s",
    f"youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://music.youtube.com/watch?v={VID}&si=abc",
    f"https://youtu.be/{VID}?si=xyz",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"  https://www.youtube.com/watch?v={VID}  ",
    # a watch link inside a list is the video, a mix list never ends
    f"https://www.youtube.com/watch?v={VID}&list=RD{VID}",
])
def test_video_links_are_recognised(raw):
    assert parse_youtube_url(raw) == {"kind": "video", "id": VID}


@pytest.mark.parametrize("raw", [
    "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
    "https://music.youtube.com/playlist?list=OLAK5uy_kAbC123",
])
def test_playlist_links_are_recognised(raw):
    link = parse_youtube_url(raw)
    assert link["kind"] == "playlist"
    assert canonical_url(link) == f"https://www.youtube.com/playlist?list={link['id']}"


@pytest.mark.parametrize("raw", [
    "daft punk around the world",
    "",
    None,
    42,
    "https://example.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
    "https://evil.test/youtube.com/watch?v=dQw4w9WgXcQ",
    "https://user:pw@youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com:8443/watch?v=dQw4w9WgXcQ",
    "file:///etc/passwd",
    "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ;rm",
    "https://www.youtube.com/channel/UC123",
    "https://www.youtube.com/@someone",
    "https://youtu.be/",
    "https://www.youtube.com/playlist?list=",
    "https://www.youtube.com/playlist?list=../../x",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ extra words",
])
def test_anything_else_is_refused(raw):
    assert parse_youtube_url(raw) is None


def test_yt_dlp_only_sees_the_rebuilt_url():
    seen = []

    def extract(url, *, playlist):
        seen.append((url, playlist))
        return {"id": VID, "title": "Artist - Song", "channel": "Artist"}

    resolve_youtube_link(f"https://youtu.be/{VID}?si=tracker&x=<script>", _client(), extract=extract)
    assert seen == [(f"https://www.youtube.com/watch?v={VID}", False)]


def test_resolve_refuses_a_non_youtube_url_before_yt_dlp():
    def extract(url, *, playlist):  # pragma: no cover - must not run
        raise AssertionError("yt-dlp was called")

    with pytest.raises(ValueError):
        resolve_youtube_link("https://example.com/song.mp3", _client(), extract=extract)


# ── metadata ──────────────────────────────────────────────────────────────

def test_a_video_is_one_row_parsed_like_a_youtube_result():
    info = {
        "id": VID, "title": "Daft Punk - Around the World (Live)", "channel": "DaftPunkVEVO",
        "duration": 245, "thumbnail": "https://i.ytimg.com/vi/x/hq.jpg",
    }
    out = resolve_youtube_link(f"https://www.youtube.com/watch?v={VID}", _client(),
                               extract=lambda url, playlist: info)
    assert out["kind"] == "video" and out["count"] == 1
    assert out["title"] == info["title"] and out["channel"] == "DaftPunkVEVO"
    row = out["results"][0]
    assert row["result_type"] == "track" and row["username"] == "youtube"
    assert row["filename"] == f"{VID}||{info['title']}"
    assert row["artist"] == "Daft Punk" and row["title"] == "Around the World (Live)"
    assert row["duration"] == 245000
    assert row["thumbnail"] == info["thumbnail"]


def test_a_video_with_no_artist_in_its_title_falls_back_to_the_channel():
    info = {"id": VID, "title": "rare remix", "channel": "Some Uploader - Topic"}
    row = resolve_youtube_link(f"https://youtu.be/{VID}", _client(),
                               extract=lambda url, playlist: info)["results"][0]
    assert row["title"] == "rare remix" and row["artist"] == "Some Uploader"


def test_music_metadata_from_yt_dlp_beats_the_title_guess():
    info = {"id": VID, "title": "SONG (official video) | label", "uploader": "label",
            "track": "Song", "artist": "Real Artist"}
    row = resolve_youtube_link(f"https://youtu.be/{VID}", _client(),
                               extract=lambda url, playlist: info)["results"][0]
    assert (row["artist"], row["title"]) == ("Real Artist", "Song")


def test_a_playlist_lists_its_videos_and_skips_the_gone_ones():
    entries = [
        {"id": "aaaaaaaaaaa", "title": "One - First", "duration": 100},
        {"id": "bbbbbbbbbbb", "title": "[Private video]"},
        {"id": "not-an-id", "title": "broken"},
        {"id": "ccccccccccc", "title": "Two - Second", "channel": "Two"},
    ]
    out = resolve_youtube_link(
        "https://www.youtube.com/playlist?list=PLabc123", _client(),
        extract=lambda url, playlist: {"title": "Mix", "uploader": "me", "entries": entries})
    assert out["kind"] == "playlist" and out["title"] == "Mix"
    assert [r["video_id"] for r in out["results"]] == ["aaaaaaaaaaa", "ccccccccccc"]
    assert out["truncated"] is False


def test_a_huge_playlist_is_capped():
    entries = [{"id": f"{i:011d}", "title": f"A - {i}"} for i in range(PLAYLIST_CAP + 1)]
    out = resolve_youtube_link(
        "https://www.youtube.com/playlist?list=PLbig", _client(),
        extract=lambda url, playlist: {"title": "Big", "entries": entries})
    assert out["count"] == PLAYLIST_CAP and out["truncated"] is True


def test_the_real_extractor_asks_for_metadata_only_and_caps_the_playlist(monkeypatch):
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls.append((url, download))
            return {"title": "x", "entries": []}

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(youtube_client, "_resolve_cookie_opts", lambda: {})
    youtube_link._extract("https://www.youtube.com/playlist?list=PLx", playlist=True)
    opts, (url, download) = calls
    assert download is False and opts["skip_download"] is True
    assert opts["extract_flat"] == "in_playlist" and opts["playlistend"] == PLAYLIST_CAP + 1


# ── download ──────────────────────────────────────────────────────────────

def test_a_link_row_downloads_through_the_youtube_client_by_id():
    """the row's filename is all /api/download hands the client, and the
    client rebuilds the watch url from the id."""
    info = {"id": VID, "title": "Artist - Song"}
    row = resolve_youtube_link(f"https://music.youtube.com/watch?v={VID}&list=x", _client(),
                               extract=lambda url, playlist: info)["results"][0]
    dispatched = []
    synced = []
    fake = SimpleNamespace(
        _engine=SimpleNamespace(worker=SimpleNamespace(
            dispatch=lambda **kw: dispatched.append(kw) or "dl-1")),
        _download_sync=lambda url, title: synced.append(url),
        current_download_id=None,
    )
    out = asyncio.run(YouTubeClient.download(fake, row["username"], row["filename"]))
    assert out == "dl-1"
    assert dispatched[0]["target_id"] == VID and dispatched[0]["source_name"] == "youtube"
    dispatched[0]["impl_callable"]("dl-1", VID, "Artist - Song")
    assert synced == [f"https://www.youtube.com/watch?v={VID}"]


# ── the route ─────────────────────────────────────────────────────────────

@pytest.fixture
def route(monkeypatch):
    web_server = pytest.importorskip("web_server")
    orch = SimpleNamespace(client=lambda name: _client() if name == "youtube" else None)
    monkeypatch.setattr(web_server, "download_orchestrator", orch)
    seen = []

    def fake_extract(url, *, playlist):
        seen.append(url)
        if playlist:
            return {"title": "Mix", "entries": [
                {"id": "aaaaaaaaaaa", "title": "One - First"},
                {"id": "bbbbbbbbbbb", "title": "Two - Second"},
            ]}
        return {"id": VID, "title": "Artist - Song", "channel": "Artist"}

    monkeypatch.setattr(youtube_link, "_extract", fake_extract)
    web_server.app.config["TESTING"] = True
    return web_server.app.test_client(), seen


def test_route_resolves_a_video(route):
    client, seen = route
    resp = client.post("/api/search/youtube-link", json={"url": f"https://youtu.be/{VID}"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind"] == "video" and body["results"][0]["filename"] == f"{VID}||Artist - Song"
    assert seen == [f"https://www.youtube.com/watch?v={VID}"]


def test_route_resolves_a_playlist(route):
    client, _ = route
    resp = client.post("/api/search/youtube-link",
                       json={"url": "https://www.youtube.com/playlist?list=PLabc123"})
    body = resp.get_json()
    assert body["kind"] == "playlist" and body["count"] == 2


@pytest.mark.parametrize("url", ["https://example.com/a.mp3", "file:///etc/passwd", "", None])
def test_route_refuses_anything_but_youtube(route, url):
    client, seen = route
    resp = client.post("/api/search/youtube-link", json={"url": url})
    assert resp.status_code == 400
    assert seen == []


def test_route_says_so_when_youtube_has_no_client(route, monkeypatch):
    import web_server
    monkeypatch.setattr(web_server, "download_orchestrator",
                        SimpleNamespace(client=lambda name: None))
    client, seen = route
    resp = client.post("/api/search/youtube-link", json={"url": f"https://youtu.be/{VID}"})
    assert resp.status_code == 503 and seen == []
