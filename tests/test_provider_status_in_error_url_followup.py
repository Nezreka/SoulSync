"""The remaining provider clients must not mistake URL digits for HTTP status.

Follow-up to PR #1391: AudioDB, Discogs, Genius, and Last.fm used the same
message-text scan for "429" to detect rate limits. An HTTP error's message
includes its URL, and entity IDs in that URL can contain those digits (e.g. a
404 on /songs/429... is not a rate limit). All four now read the structured
status first via core.http_error_status, keeping message matching only for
exceptions with no structured status.
"""

import threading
import time

import pytest
import requests

import core.audiodb_client as audiodb
import core.discogs_client as discogs
import core.genius_client as genius
import core.lastfm_client as lastfm


def _http_error(status, url):
    response = requests.Response()
    response.status_code = status
    response.url = url
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        assert "429" in str(e) or "503" in str(e), \
            "test setup: URL digits must appear in the message"
        return e
    raise AssertionError("raise_for_status did not raise")


def _capture_sleep(monkeypatch, module):
    sleeps = []
    current_thread = threading.current_thread()
    real_sleep = time.sleep

    def capture(delay):
        if threading.current_thread() is current_thread:
            sleeps.append(delay)
        else:
            real_sleep(delay)

    monkeypatch.setattr(module.time, "sleep", capture)
    return sleeps


@pytest.mark.parametrize("url", [
    "https://www.theaudiodb.com/api/v1/json/2/album.php?m=429871",
    "https://www.theaudiodb.com/api/v1/json/2/search.php?s=item503",
])
def test_audiodb_404_with_status_digits_does_not_back_off(monkeypatch, url):
    error = _http_error(404, url)
    sleeps = _capture_sleep(monkeypatch, audiodb)
    monkeypatch.setattr(audiodb, "_last_api_call_time", 0)

    @audiodb.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == []


def test_audiodb_real_429_still_backs_off(monkeypatch):
    error = _http_error(429, "https://www.theaudiodb.com/api/v1/json/2/album.php?m=123")
    sleeps = _capture_sleep(monkeypatch, audiodb)
    monkeypatch.setattr(audiodb, "_last_api_call_time", 0)

    @audiodb.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == [4.0]


def test_audiodb_statusless_429_message_still_backs_off(monkeypatch):
    sleeps = _capture_sleep(monkeypatch, audiodb)
    monkeypatch.setattr(audiodb, "_last_api_call_time", 0)

    @audiodb.rate_limited
    def fetch():
        raise RuntimeError("429 too many requests")

    with pytest.raises(RuntimeError):
        fetch()
    assert sleeps == [4.0]


@pytest.mark.parametrize("url", [
    "https://api.discogs.com/artists/429001",
    "https://api.discogs.com/releases/50322",
])
def test_discogs_404_with_status_digits_does_not_back_off(monkeypatch, url):
    error = _http_error(404, url)
    sleeps = _capture_sleep(monkeypatch, discogs)
    monkeypatch.setattr(discogs, "_last_api_call_time", 0)

    @discogs.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == []


def test_discogs_real_429_still_backs_off(monkeypatch):
    error = _http_error(429, "https://api.discogs.com/artists/123")
    sleeps = _capture_sleep(monkeypatch, discogs)
    monkeypatch.setattr(discogs, "_last_api_call_time", 0)

    @discogs.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == [30]


@pytest.mark.parametrize("url", [
    "https://api.genius.com/songs/429001",
    "https://api.genius.com/songs/50322",
])
def test_genius_404_with_status_digits_does_not_gate(monkeypatch, url):
    error = _http_error(404, url)
    monkeypatch.setattr(genius, "_last_api_call_time", 0)
    monkeypatch.setattr(genius, "_rate_limit_until", 0)
    monkeypatch.setattr(genius, "_rate_limit_backoff", 0)

    @genius.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert genius._rate_limit_until == 0


def test_genius_real_429_still_gates(monkeypatch):
    error = _http_error(429, "https://api.genius.com/songs/123")
    monkeypatch.setattr(genius, "_last_api_call_time", 0)
    monkeypatch.setattr(genius, "_rate_limit_until", 0)
    monkeypatch.setattr(genius, "_rate_limit_backoff", 0)

    @genius.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert genius._rate_limit_until > 0


@pytest.mark.parametrize("url", [
    "https://ws.audioscrobbler.com/2.0/?method=track.getInfo&track=item429",
    "https://ws.audioscrobbler.com/2.0/?method=track.getInfo&track=item503",
])
def test_lastfm_404_with_status_digits_does_not_back_off(monkeypatch, url):
    error = _http_error(404, url)
    sleeps = _capture_sleep(monkeypatch, lastfm)
    monkeypatch.setattr(lastfm, "_last_api_call_time", 0)

    @lastfm.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == []


def test_lastfm_real_429_still_backs_off(monkeypatch):
    error = _http_error(429, "https://ws.audioscrobbler.com/2.0/?method=track.getInfo")
    sleeps = _capture_sleep(monkeypatch, lastfm)
    monkeypatch.setattr(lastfm, "_last_api_call_time", 0)

    @lastfm.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == [5.0]
