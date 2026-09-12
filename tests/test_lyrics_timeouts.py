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
    client = LyricsClient()
    assert client.api is not None
    assert client._fetch_remote_lyrics('Song', 'Artist', 'Album', 180) is None
    assert len(timeouts) == 2  # exact lookup and search fallback
    assert all(isinstance(t, tuple) and 0 < t[0] <= 5 and 0 < t[1] <= 10 for t in timeouts), timeouts


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
