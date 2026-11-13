"""Credentials never reach a log line.

Nov 13 2026: a TMDB 404 was logged with `logger.exception`, and the text of a
`requests` HTTPError is the URL that failed, so `?api_key=<the real key>` went
into app.log with the traceback. Logs are what people paste into an issue.
Every formatter now passes the finished line, traceback included, through
`redact`."""

from __future__ import annotations

import io
import logging

import pytest

from utils.logging_config import ColoredFormatter, SafeFormatter, redact

KEY = "dd0000000000000000000000000000ab"


@pytest.mark.parametrize("line", [
    f"404 Client Error: Not Found for url: https://api.themoviedb.org/3/movie/1?api_key={KEY}&append_to_response=credits",
    f"https://www.googleapis.com/youtube/v3/videos?part=snippet&key={KEY}",
    f"GET http://plex.local:32400/library/sections?X-Plex-Token={KEY}",
    f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&api_key={KEY}&api_sig={KEY}&sk={KEY}",
    f"https://accounts.spotify.com/api/token?client_secret={KEY}&refresh_token={KEY}",
    f"https://www.qobuz.com/api.json/0.2/track/getFileUrl?track_id=5&request_sig={KEY}&user_auth_token={KEY}",
    f"http://nd.local/rest/ping.view?u=boulder&t={KEY}&s={KEY}&v=1.16.1&c=soulsync",
    f"http://nd.local/rest/ping.view?u=boulder&p={KEY}&v=1.16.1",
    f"headers={{'Authorization': 'Bearer {KEY}', 'Accept': 'application/json'}}",
    f"Authorization: Bearer {KEY}",
    f'Authorization: MediaBrowser Token="{KEY}"',
    f"sent Bearer {KEY} and got 401",
    f"http://user:{KEY}@qbittorrent.local:8080/api/v2/torrents/info",
    f"config: {{'api_key': '{KEY}', 'base_url': 'http://x'}}",
    f'payload {{"password": "{KEY}", "username": "boulder"}}',
    f"jellyfin http://jf.local/Items?api_key={KEY}",
    f"prowlarr http://p.local/api/v1/search?apikey={KEY}&query=x",
])
def test_a_credential_value_is_replaced(line):
    out = redact(line)
    assert KEY not in out
    assert "***" in out


@pytest.mark.parametrize("line", [
    "Slow request: GET /api/discover/list?kind=movie&genre=10749&sort=popularity.desc -> 200 in 2388.6ms",
    "Qobuz API error: album/get returned HTTP 400 for {'album_id': 'l694lsf1hyqsb', 'extra': 'tracks'}",
    "refresh token successfully renewed for profile 3",
    "basic authentication is not configured",
    "the monkey=banana&donkey=1 parameters",
    "GET /api/search?q=the+key+of+life&page=2&limit=50",
    "Slow request: GET /api/video/discover/list?key=now_playing&pages=2 -> 200 in 1473.6ms",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "Matched track 'Token' -> Qobuz ID: 460290565",
    "slow query 2.7s at core/library2/worker_queue.py:332 _pending_count: SELECT COUNT(*) FROM x WHERE a.service = :service",
    "H:\\Music\\Rezz\\09 - HOW I DO IT.mp3",
    "",
])
def test_an_ordinary_line_is_left_alone(line):
    assert redact(line) == line


def test_the_parameter_name_stays_so_the_line_still_reads():
    out = redact(f"https://api.themoviedb.org/3/collection/9?api_key={KEY}&language=en")
    assert out == "https://api.themoviedb.org/3/collection/9?api_key=***&language=en"


@pytest.mark.parametrize("formatter", [SafeFormatter, ColoredFormatter])
def test_a_logged_exception_is_redacted_traceback_and_all(formatter):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter("%(levelname)s - %(message)s"))
    log = logging.getLogger(f"redaction-test-{formatter.__name__}")
    log.handlers[:] = [handler]
    log.propagate = False
    try:
        raise RuntimeError(f"404 Client Error for url: https://api.themoviedb.org/3/movie/1?api_key={KEY}")
    except RuntimeError as exc:
        log.exception("tmdb_detail failed for movie 1: %s", exc)
    text = stream.getvalue()
    assert "tmdb_detail failed" in text and "Traceback" in text
    assert KEY not in text
    assert text.count("api_key=***") >= 2   # the message and the traceback


def test_every_file_log_goes_through_the_redacting_formatter():
    """app.log, source_reuse.log, post_processing.log and acoustid.log."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("web_server.py", "core/acoustid_client.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "setFormatter(_logging.Formatter(" not in source, name
        assert "setFormatter(logging.Formatter(" not in source, name


def test_the_log_viewer_blanks_lines_that_were_written_before_the_fix():
    """keys already in app.log on disk are not shown, or copied, from Settings"""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parent.parent / "web_server.py").read_text(encoding="utf-8")
    start = source.index("def get_log_tail():")
    body = source[start:source.index("\n@app.route", start)]
    assert "result_lines.append(_redact_log_line(stripped))" in body
    assert "result_lines.append(stripped)" not in body
