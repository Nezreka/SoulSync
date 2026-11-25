"""the authorization battery (PAIRING_SPEC.md 4, 8): every rule of the real
app, every method, called with a device token. a route a device may use has
to opt in (@device_pick / @device_playback, core/connect/device_access.py);
every other one refuses before its view runs.

the opted-in routes are pinned below. opting a route in is a decision, so
it changes this list in the same change, where a reviewer sees it.
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest

web_server = pytest.importorskip("web_server")

from core.connect import device_access as A  # noqa: E402
from core.connect import device_tokens as T  # noqa: E402

# endpoint -> mark. the whole allow-list: add here only with the route
OPTED_IN = {
    "devices.device_me": A.PICK,
    "devices.device_pick_list": A.PICK,
    "devices.device_pick_profile": A.PICK,
    # music apps: every Subsonic method is one route; the methods themselves
    # are pinned in tests/subsonic/test_methods.py
    "subsonic.rest": A.PLAYBACK,
    # the player's playback (core/player/playable.py): a music app plays
    # tracks through these, as its profile
    "player_stream.playable": A.PLAYBACK,
    "player_stream.stream": A.PLAYBACK,
    "player_stream.transcode": A.PLAYBACK,
    "player_stream.transcode_start": A.PLAYBACK,
    "player_tracks.tracks": A.PLAYBACK,
    "player_tracks.subtitle": A.PLAYBACK,
    "player_tracks.lyrics": A.PLAYBACK,
    # full /player app routes (caller profile playback/browse state only)
    "player.get_progress": A.PLAYBACK,
    "player.put_progress": A.PLAYBACK,
    "player.lookup_progress": A.PLAYBACK,
    "player.finish_progress": A.PLAYBACK,
    "player.mark_watched": A.PLAYBACK,
    "player.delete_progress": A.PLAYBACK,
    "player.continue_rail": A.PLAYBACK,
    "player.video_catalog": A.PLAYBACK,
    "player.collections": A.PLAYBACK,
    "player.youtube_home": A.PLAYBACK,
    "player.youtube_channel": A.PLAYBACK,
    "player.music_channels": A.PLAYBACK,
    "player.get_favorites": A.PLAYBACK,
    "player.put_favorites": A.PLAYBACK,
    "player.get_watchlist": A.PLAYBACK,
    "player.add_to_watchlist": A.PLAYBACK,
    "player.remove_from_watchlist": A.PLAYBACK,
    "player.get_home_layout": A.PLAYBACK,
    "player.put_home_layout": A.PLAYBACK,
    "player.reset_home_layout": A.PLAYBACK,
    "player.maker_titles": A.PLAYBACK,
    "player.title_logos": A.PLAYBACK,
    "player.keyword_titles": A.PLAYBACK,
    "player.player_playlists": A.PLAYBACK,
    "player.player_playlist": A.PLAYBACK,
    "user_playlists.add_user_playlist_tracks": A.PLAYBACK,
    "user_playlists.remove_matching_user_playlist_tracks": A.PLAYBACK,
    "user_playlists.like_songs": A.PLAYBACK,
    "user_playlists.unlike_songs": A.PLAYBACK,
    "player.player_discover_playlists": A.PLAYBACK,
    "player.player_discover_playlist": A.PLAYBACK,
    "player.artist_top_tracks": A.PLAYBACK,
    "player.get_player_settings": A.PLAYBACK,
    "player.put_player_settings": A.PLAYBACK,
    "player.presence_beat": A.PLAYBACK,
    "player.presence_stop": A.PLAYBACK,
    "player.presence_list": A.PLAYBACK,
    "player.presence_together": A.PLAYBACK,
    "player.list_notes": A.PLAYBACK,
    "player.add_note": A.PLAYBACK,
    "player.edit_note": A.PLAYBACK,
    "player.delete_note": A.PLAYBACK,
    "player.guide_channels": A.PLAYBACK,
    "player.add_guide_channel": A.PLAYBACK,
    "player.edit_guide_channel": A.PLAYBACK,
    "player.remove_guide_channel": A.PLAYBACK,
    "player.get_guide_lineup": A.PLAYBACK,
    "player.put_guide_lineup": A.PLAYBACK,
    "player.guide_watched": A.PLAYBACK,
    # the evening's premieres: server-decided, every device shows the same
    "player.guide_premieres": A.PLAYBACK,
    "player.guide_people": A.PLAYBACK,
    "player.guide_person": A.PLAYBACK,
    "player.guide_reminders": A.PLAYBACK,
    "player.add_guide_reminder": A.PLAYBACK,
    "player.remove_guide_reminder": A.PLAYBACK,
    "player.player_downloads_list": A.PLAYBACK,
    "player.player_downloads_log": A.PLAYBACK,
    "player.player_downloads_removed": A.PLAYBACK,
    "player.player_admin_users": A.PLAYBACK,
    "player.player_admin_user_downloads": A.PLAYBACK,
    # read-only browse, detail, and artwork GET routes used by /player
    "video_api.video_poster": A.PLAYBACK,
    "video_api.video_backdrop": A.PLAYBACK,
    "video_api.video_show_detail": A.PLAYBACK,
    "video_api.video_movie_detail": A.PLAYBACK,
    "video_api.video_person_detail": A.PLAYBACK,
    "video_api.video_detail_extras": A.PLAYBACK,
    "video_api.video_episode_extra": A.PLAYBACK,
    "video_api.video_calendar": A.PLAYBACK,
    "video_api.video_calendar_followed": A.PLAYBACK,
    "video_api.video_issues_list": A.PLAYBACK,
    "video_api.video_issues_create": A.PLAYBACK,
    "lib2_list_albums": A.PLAYBACK,
    "lib2_list_songs": A.PLAYBACK,
    "lib2_list_artists": A.PLAYBACK,
    "lib2_get_artist": A.PLAYBACK,
    "lib2_get_album": A.PLAYBACK,
    "lib2_artwork": A.PLAYBACK,
    "discover_routes.image_proxy": A.PLAYBACK,
    "discover_routes.serve_cached_image": A.PLAYBACK,
    "podcasts_api.show_detail": A.PLAYBACK,
    "podcasts_api.podcasts_watchlist_list": A.PLAYBACK,
    "audiobooks_api.library": A.PLAYBACK,
    "audiobooks_api.library_cover": A.PLAYBACK,
    "video_api.subtitles_search": A.PLAYBACK,
    "video_api.subtitles_manual_download": A.PLAYBACK,
    "profile_admin.get_avatar": A.PLAYBACK,
    "video_api.video_request_create": A.PLAYBACK,
    "video_api.video_request_options": A.PLAYBACK,
    "player.request_music_track": A.PLAYBACK,
    "devices.device_switch_profiles": A.PLAYBACK,
    "devices.device_switch_profile": A.PLAYBACK,
    "player_stream.create_cast_token": A.PLAYBACK,
    "player.list_watch_parties": A.PLAYBACK,
    "player.create_watch_party": A.PLAYBACK,
    "player.get_watch_party": A.PLAYBACK,
    "player.join_watch_party": A.PLAYBACK,
    "player.leave_watch_party": A.PLAYBACK,
    "player.update_watch_party_state": A.PLAYBACK,
    "player.post_watch_party_chat": A.PLAYBACK,
    "player.delete_watch_party": A.PLAYBACK,
    "player.collection_poster": A.PLAYBACK,
    "player.get_commercials_config": A.PLAYBACK,
    "player.put_commercials_config": A.PLAYBACK,
    "player.get_commercials_status": A.PLAYBACK,
    "player.warm_commercials": A.PLAYBACK,
    "player.get_commercial_break": A.PLAYBACK,
    "player.schedule_commercial_breaks": A.PLAYBACK,
    "player.clear_commercials_cache": A.PLAYBACK,
    "player.player_issues_list": A.PLAYBACK,
    "player.player_issues_create": A.PLAYBACK,
    "player.player_issues_update": A.PLAYBACK,
    "player.player_issues_delete": A.PLAYBACK,
    "video_api.video_img_proxy": A.PLAYBACK,
}

IGNORED = {"HEAD", "OPTIONS"}
_ARG = re.compile(r"<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>")


def _sample_path(rule) -> str:
    def sub(m):
        conv = m.group(1) or "string"
        return "1" if conv in ("int", "float") else "x"
    return _ARG.sub(sub, rule.rule)


def _calls(app):
    """(method, path, the endpoint that path really reaches) for every rule."""
    adapter = app.url_map.bind("localhost")
    out = []
    for rule in app.url_map.iter_rules():
        path = _sample_path(rule)
        for method in sorted((rule.methods or set()) - IGNORED):
            try:
                endpoint, _ = adapter.match(path, method=method)
            except Exception:  # noqa: BLE001 - a redirect or no match: still must refuse
                endpoint = None
            out.append((method, path, endpoint))
    return out


def _marks(app):
    return {ep: A.device_access(fn) for ep, fn in app.view_functions.items() if A.device_access(fn)}


@pytest.fixture(scope="module")
def tokens():
    db = web_server.get_database()
    pid = db.create_profile("battery_%s" % uuid4().hex[:6], "#123456", None, is_admin=False)
    live, live_id = T.create(db, name="battery live")
    assert T.pick_profile(db, live_id, pid)
    admin_dev, admin_id = T.create(db, name="battery admin")
    assert T.pick_profile(db, admin_id, 1)
    unprofiled, _ = T.create(db, name="battery waiting")
    revoked, revoked_id = T.create(db, name="battery revoked")
    T.revoke(db, revoked_id)
    yield {"live": live, "admin": admin_dev, "unprofiled": unprofiled, "revoked": revoked}
    db.delete_profile(pid)
    T.revoke(db, admin_id)


def test_the_allow_list_is_exactly_the_marked_routes():
    assert _marks(web_server.app) == OPTED_IN


def test_no_admin_route_opts_in():
    from core.permissions import declared_access
    for ep in OPTED_IN:
        assert declared_access(web_server.app.view_functions[ep]) not in (("admin", None),), ep


def _refused(client, token, method, path):
    r = client.open(path, method=method, headers={"X-Device-Token": token}, json={})
    # read the body whatever its content type: a .js path is relabelled as
    # javascript on the way out (_force_module_script_mimetype)
    try:
        body = json.loads(r.get_data(as_text=True) or "{}")
    except ValueError:
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {}).get("error")


@pytest.mark.parametrize("who", ["live", "admin"])
def test_a_picked_device_is_refused_everywhere_it_has_not_opted_in(tokens, who):
    client = web_server.app.test_client()
    calls = _calls(web_server.app)
    assert len(calls) > 500  # the whole app, not a stub
    leaks = []
    for method, path, endpoint in calls:
        if endpoint in OPTED_IN:
            continue
        status, code = _refused(client, tokens[who], method, path)
        if (status, code) != (403, "device_not_allowed"):
            leaks.append((method, path, endpoint, status, code))
    assert not leaks, "a device reached routes that never opted in:\n" + "\n".join(map(str, leaks[:40]))


def test_an_unprofiled_device_reaches_only_pick_routes(tokens):
    client = web_server.app.test_client()
    leaks = []
    for method, path, endpoint in _calls(web_server.app):
        if OPTED_IN.get(endpoint) == A.PICK:
            continue
        status, code = _refused(client, tokens["unprofiled"], method, path)
        if status != 403 or code not in ("device_pick_first", "device_not_allowed"):
            leaks.append((method, path, endpoint, status, code))
    assert not leaks, "\n".join(map(str, leaks[:40]))


def test_a_revoked_device_reaches_nothing(tokens):
    client = web_server.app.test_client()
    leaks = []
    for method, path, endpoint in _calls(web_server.app):
        status, code = _refused(client, tokens["revoked"], method, path)
        if (status, code) != (401, "device_token_invalid"):
            leaks.append((method, path, endpoint, status, code))
    assert not leaks, "\n".join(map(str, leaks[:40]))


def test_an_unknown_path_is_refused_too(tokens):
    client = web_server.app.test_client()
    assert _refused(client, tokens["live"], "GET", "/api/no/such/route") == (403, "device_not_allowed")
    assert _refused(client, tokens["live"], "GET", "/static/style.css") == (403, "device_not_allowed")
