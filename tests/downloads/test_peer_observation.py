"""Short-lived Soulseek peer observations never become permanent bans."""

from core.downloads import peer_observation as observations


def test_observation_requires_moving_sample_and_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(observations.time, 'monotonic', lambda: now[0])
    peer = 'observed-test-peer'
    observations._observations.pop(peer, None)

    observations.observe_peer(peer, 20_000, 5)
    assert observations.peer_speed(peer) is None

    observations.observe_peer(peer, 0, 60)
    assert observations.peer_speed(peer) is None

    observations.observe_peer(peer, 20_000, 60)
    assert observations.peer_speed(peer) == 20_000
    observations.observe_peer(peer, 1_000_000, 60)
    assert 20_000 < observations.peer_speed(peer) < 1_000_000

    now[0] += observations._TTL_SECONDS + 1
    assert observations.peer_speed(peer) is None
