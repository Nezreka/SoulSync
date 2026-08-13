"""Play through the MEDIA SERVER, not through a mount SoulSync may not have.

Boulder, on being told the watch party needed path mappings: *"does it have to
stream it via the mounted path? why not just stream it through the server? like we
have the api"*. Correct, and it makes the mapping unnecessary for playback.

The numbers behind it, from his live database: Plex reports files under **eleven**
mount roots (/mnt/easystore1-5, /mnt/plex_20tb, /mnt/seagate_expansion1-2,
/mnt/20tb_drive2, /mnt/md2 — 120,821 files) while SoulSync's `video_base_dirs`
returns three paths under a single SMB share. `resolve_video_file_path` could only
ever find the ~11,697 files on `plex_20tb`: under 10% of the library. Everything
else answered "that file is in your library but this server can't reach it".

The media server has no such problem — it *reported* those paths — and SoulSync
already stores each item's `server_id` alongside the server's URL and token. So the
order is: local file when it happens to resolve (no proxy, no load on the server,
survives the server being down), otherwise ask the server.

Two things these tests hold down hard:
  · **`static=true` on Jellyfin.** A transcoded stream has no stable byte offsets,
    so Range requests stop meaning anything — and Range is how a latecomer joins a
    showing already in progress.
  · **The upstream URL never reaches the browser.** It carries the server's token.
    SoulSync relays the bytes instead.
"""

from __future__ import annotations

from core.video.media_stream import (
    jellyfin_stream_url,
    plex_part_url,
    server_stream_target,
)


class _DB:
    def __init__(self, ref=None):
        self._ref = ref or {}

    def video_server_ref(self, kind, *, tmdb_id=None, season=None, episode=None):
        return dict(self._ref)


# ── URL shapes ───────────────────────────────────────────────────────────────

def test_jellyfin_asks_for_the_original_file():
    u = jellyfin_stream_url("http://jf:8096/", "abc123", "KEY")
    assert u == "http://jf:8096/Videos/abc123/stream?static=true&api_key=KEY"


def test_jellyfin_static_is_not_optional():
    """Without static=true Jellyfin may transcode, and a transcoded stream has
    no stable byte offsets — seeking, and therefore joining a showing already in
    progress, quietly stops working."""
    assert "static=true" in jellyfin_stream_url("http://jf", "1", "K")


def test_plex_part_urls_carry_the_token():
    u = plex_part_url("http://plex:32400", "/library/parts/99/1700/file.mkv", "TOK")
    assert u == "http://plex:32400/library/parts/99/1700/file.mkv?X-Plex-Token=TOK"


def test_plex_part_url_survives_an_existing_query_string():
    u = plex_part_url("http://plex:32400", "/library/parts/9/file.mkv?download=0", "TOK")
    assert u.endswith("?download=0&X-Plex-Token=TOK")


def test_plex_part_url_tolerates_a_missing_leading_slash():
    assert plex_part_url("http://p", "library/parts/1/f.mkv", "T").startswith(
        "http://p/library/parts/")


def test_trailing_slashes_do_not_double_up():
    assert "//Videos" not in jellyfin_stream_url("http://jf:8096/", "1", "K")
    assert "32400//library" not in plex_part_url("http://plex:32400/", "/library/x", "T")


# ── choosing a target ────────────────────────────────────────────────────────

def test_an_unowned_item_has_no_server_target():
    assert server_stream_target(_DB({}), "movie", 603) is None


def test_a_row_without_a_server_id_has_no_target():
    assert server_stream_target(_DB({"server_source": "plex", "server_id": None}),
                                "movie", 603) is None


def test_an_unknown_server_source_is_not_guessed_at(monkeypatch):
    db = _DB({"server_source": "kodi", "server_id": "5"})
    assert server_stream_target(db, "movie", 603) is None


def test_jellyfin_target_is_built_from_the_stored_id(monkeypatch):
    import core.video.sources as sources
    monkeypatch.setattr(sources, "video_jellyfin_config",
                        lambda db=None: {"base_url": "http://jf:8096", "api_key": "KEY"})
    out = server_stream_target(_DB({"server_source": "jellyfin", "server_id": "itm9"}),
                               "movie", 603)
    assert out == {"url": "http://jf:8096/Videos/itm9/stream?static=true&api_key=KEY",
                   "server": "jellyfin"}


def test_an_unconfigured_server_yields_nothing_rather_than_a_broken_url(monkeypatch):
    import core.video.sources as sources
    monkeypatch.setattr(sources, "video_jellyfin_config",
                        lambda db=None: {"base_url": "", "api_key": ""})
    assert server_stream_target(_DB({"server_source": "jellyfin", "server_id": "x"}),
                                "movie", 603) is None


def test_a_db_that_raises_degrades_to_no_target():
    class _Boom:
        def video_server_ref(self, *a, **k):
            raise RuntimeError("db is having a moment")
    assert server_stream_target(_Boom(), "movie", 603) is None


# ── the endpoint's contract ──────────────────────────────────────────────────

def _watch_src() -> str:
    import inspect
    from pathlib import Path
    return Path(inspect.getfile(__import__("api.video.watch", fromlist=["x"]))).read_text(
        encoding="utf-8")


def _route(name: str) -> str:
    """Just ONE route's body. Slicing to end-of-file reads the routes that
    follow, which turns 'this route must not do X' into 'no later route may
    mention X' — a false failure waiting to happen."""
    src = _watch_src()
    start = src.index('@bp.route("/watch/%s"' % name)
    nxt = src.find("@bp.route(", start + 10)
    return src[start:nxt if nxt != -1 else len(src)]


def test_a_local_file_is_preferred_over_the_server():
    """Cheapest and most robust when it works: no proxy hop, no load on the
    media server, and it still plays when that server is down."""
    src = _watch_src()
    body = src[src.index("def _party_file("):src.index('@bp.route("/watch/playable"')]
    assert body.index("resolve_video_file_path") < body.index("server_stream_target")


def test_the_upstream_url_is_never_handed_to_the_browser():
    """It carries the media server's token. SoulSync relays the bytes."""
    stream = _route("stream")
    assert "stream_with_context" in stream and "iter_content" in stream
    assert "redirect" not in stream, "a redirect would leak the token to the client"


def test_range_is_passed_both_ways():
    """Seeking is how a latecomer joins a showing already in progress, so the
    Range request goes up and Content-Range comes back."""
    stream = _route("stream")
    assert 'request.headers.get("Range")' in stream
    assert "content-range" in stream and "accept-ranges" in stream


def test_a_dead_media_server_is_reported_not_swallowed():
    stream = _route("stream")
    assert "502" in stream and "didn't answer" in stream


def test_the_upstream_response_is_always_closed():
    """A streamed requests response holds a pooled connection until closed —
    leaking one per party would exhaust the pool."""
    stream = _route("stream")
    assert "finally:" in stream and "up.close()" in stream


# ── multi-version items: judge and play the SAME file ────────────────────────
# 2,199 movies in the live library carry more than one media file (up to five).
# video_stored_file_path picks the LARGEST and the codec verdict is computed
# from that row, so taking Plex's first part would judge one version and stream
# another — and the "your browser won't like this" warning would describe a copy
# nobody is watching.

class _Part:
    def __init__(self, key, size=0, file=""):
        self.key, self.size, self.file = key, size, file


class _Media:
    def __init__(self, parts):
        self.parts = parts


class _Item:
    def __init__(self, media):
        self.media = media


def _fake_plex(monkeypatch, item):
    import core.video.media_stream as ms

    class _Srv:
        def __init__(self, *a, **k):
            pass

        def fetchItem(self, _key):
            return item

    import sys, types
    mod = types.ModuleType("plexapi.server")
    mod.PlexServer = _Srv
    monkeypatch.setitem(sys.modules, "plexapi.server", mod)
    return ms


def test_the_part_is_matched_by_size(monkeypatch):
    ms = _fake_plex(monkeypatch, _Item([_Media([
        _Part("/library/parts/1/small.mkv", size=700_000_000),
        _Part("/library/parts/2/big.mkv", size=8_000_000_000),
    ])]))
    got = ms._plex_part_key("http://p", "T", 1, want_size=8_000_000_000)
    assert got == "/library/parts/2/big.mkv"


def test_the_part_falls_back_to_the_stored_filename(monkeypatch):
    ms = _fake_plex(monkeypatch, _Item([_Media([
        _Part("/library/parts/1/a.mkv", size=1, file="/mnt/x/A Movie (2020) 720p.mkv"),
        _Part("/library/parts/2/b.mkv", size=2, file="/mnt/x/A Movie (2020) 1080p.mkv"),
    ])]))
    got = ms._plex_part_key("http://p", "T", 1, want_size=None,
                            want_path="/mnt/other/A Movie (2020) 1080p.mkv")
    assert got == "/library/parts/2/b.mkv"


def test_a_windows_stored_path_still_matches_a_posix_part(monkeypatch):
    ms = _fake_plex(monkeypatch, _Item([_Media([
        _Part("/library/parts/9/f.mkv", size=5, file="/mnt/easystore4/Film.mkv"),
    ])]))
    assert ms._plex_part_key("http://p", "T", 1,
                             want_path="\\\\nas\\share\\Film.mkv") == "/library/parts/9/f.mkv"


def test_no_match_degrades_to_the_first_part_rather_than_nothing(monkeypatch):
    """Playing the wrong version beats playing nothing — but it is logged,
    because a silent mismatch is how the verdict starts lying."""
    ms = _fake_plex(monkeypatch, _Item([_Media([
        _Part("/library/parts/1/a.mkv", size=1),
        _Part("/library/parts/2/b.mkv", size=2),
    ])]))
    assert ms._plex_part_key("http://p", "T", 1, want_size=999) == "/library/parts/1/a.mkv"


def test_a_single_version_item_is_unaffected(monkeypatch):
    ms = _fake_plex(monkeypatch, _Item([_Media([_Part("/library/parts/7/only.mkv", size=42)])]))
    assert ms._plex_part_key("http://p", "T", 1, want_size=None) == "/library/parts/7/only.mkv"


def test_an_item_with_no_parts_yields_nothing(monkeypatch):
    ms = _fake_plex(monkeypatch, _Item([_Media([])]))
    assert ms._plex_part_key("http://p", "T", 1) is None


def test_the_endpoint_hands_over_the_file_it_judged():
    src = _watch_src()
    body = src[src.index("def _party_file("):src.index('@bp.route("/watch/playable"')]
    assert "want_size=hit.get(\"size_bytes\")" in body
    assert "want_path=hit.get(\"path\")" in body
