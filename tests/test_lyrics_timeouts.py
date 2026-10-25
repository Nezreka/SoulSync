"""Issue 1245: lyrics must not hold imports or player requests indefinitely."""
import pytest
import requests

from core.lyrics_client import LyricsClient


def test_both_lrclib_lookups_have_transport_timeouts(monkeypatch):
    timeouts = []
    def stalled(self, method, url, **kwargs):
        timeouts.append(kwargs.get('timeout'))
        raise requests.exceptions.ReadTimeout('simulated unresponsive lyrics service')
    monkeypatch.setattr(requests.Session, 'request', stalled)
    # Isolate from the shared LRCLib throttle: this test pins TRANSPORT
    # timeouts, not pacing (#1879) — and throttle state must not leak
    # into (or out of) this test.
    from core import lrclib_throttle
    lrclib_throttle._reset_for_tests()
    monkeypatch.setattr(lrclib_throttle, 'wait_for_slot', lambda **k: True)
    client = LyricsClient()
    client._sleep = lambda s: None  # don't wait out the retry backoff
    assert client.api is not None
    assert client._fetch_remote_lyrics('Song', 'Artist', 'Album', 180) is None
    # #1879: a timeout is TRANSIENT, so the exact lookup is retried a
    # bounded number of times (3 attempts) and the search fallback never
    # fires — the old behaviour fired search immediately after the exact
    # call failed, doubling requests against a struggling service.
    assert len(timeouts) == 3
    assert all(isinstance(t, tuple) and 0 < t[0] <= 5 and 0 < t[1] <= 10 for t in timeouts), timeouts
    # And the tri-state lookup reports it as unknown, not absent.
    result = client.lookup_remote_lyrics('Song', 'Artist', 'Album', 180)
    assert result.status == 'unknown' and result.error_kind == 'timeout'


def test_player_direct_api_also_uses_timeout(monkeypatch):
    timeouts = []
    def stalled(self, method, url, **kwargs):
        timeouts.append(kwargs.get('timeout'))
        raise requests.exceptions.ReadTimeout('simulated lyrics timeout')
    monkeypatch.setattr(requests.Session, 'request', stalled)
    client = LyricsClient()
    with pytest.raises(requests.exceptions.ReadTimeout):
        client.api.search_lyrics(track_name='Song', artist_name='Artist')
    assert timeouts == [(3.05, 10)]
