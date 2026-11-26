"""#2301: a member who hasn't connected their own account doesn't get the
server's playlists."""

import pytest

from core.playlists import account_isolation as ai


def _r(service="spotify", **kw):
    base = dict(profile_id=3, is_admin=False, own_connected=False, shared=False)
    base.update(kw)
    return ai.refusal(service, **base)


def test_a_member_with_nothing_connected_is_asked_to_connect_their_own():
    out = _r("spotify")
    assert out["needs_own_account"] is True and out["can_connect"] is True
    assert "Connect your own Spotify account" in out["error"]
    assert _r("tidal")["can_connect"] is True


def test_a_service_only_the_server_has_says_it_is_not_shared():
    for service, name in (("deezer", "Deezer"), ("qobuz", "Qobuz"), ("ytmusic", "YouTube Music")):
        out = _r(service)
        assert out["can_connect"] is False and name in out["error"] and "isn't shared" in out["error"]


@pytest.mark.parametrize("who", [
    dict(is_admin=True), dict(profile_id=1), dict(profile_id=None),
    dict(own_connected=True), dict(shared=True),
])
def test_everyone_else_reads_the_list_as_before(who):
    assert _r("spotify", **who) is None
    assert _r("deezer", **who) is None


def test_outside_a_request_nothing_is_refused():
    # a worker syncing a mirrored playlist, an automation: the server itself
    assert ai.refusal_for_request("spotify") is None
