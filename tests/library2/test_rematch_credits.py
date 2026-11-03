"""a rematch takes the old match's guests with it, and brings the new ones.

3.6.1 kept spotify/deezer credits in their own table and a rematch dropped
that source's rows and queued a refetch (test_album_rematch_drops_credits_
and_queues_it). on library v2 the credits are featured rows on the shared
junctions, and nothing ever removed them: matching "otis" to the wrong
release and fixing it left the wrong release's guests on the track, and the
right release's guests never came, because the worker treats a manual match
as settled and never comes back to it.
"""

from __future__ import annotations

import json

import pytest

from core.library2 import provider_credits
from core.library2.match_status import apply_manual_match, set_library_v2_match
from core.library2.provider_credits import (drop_source_credits, link_credited_artists,
                                            refresh_match_credits, schedule_match_credits)
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


def _world(tmp_path, *, title="Otis", track_artist=None):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    jay = seed_artist(conn, server_id="jay", name="Jay-Z", server_source="soulsync")
    kanye = seed_artist(conn, server_id="ye", name="Kanye West", server_source="soulsync")
    frank = seed_artist(conn, server_id="fo", name="Frank Ocean", server_source="soulsync")
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-jay', external_ids=? WHERE id=?",
                 (json.dumps({"deezer": "dz-jay"}), jay))
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-ye', external_ids=? WHERE id=?",
                 (json.dumps({"deezer": "dz-ye"}), kanye))
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-frank' WHERE id=?", (frank,))
    album = seed_album(conn, server_id="wtt", title="Watch the Throne", artist_id=jay,
                       server_source="soulsync")
    track = seed_track(conn, server_id="otis", title=title, album_id=album, artist_id=jay,
                       server_source="soulsync", track_artist=track_artist)
    conn.commit()
    return db, conn, {"jay": jay, "kanye": kanye, "frank": frank,
                      "album": album, "track": track}


def _track_credits(conn, track):
    return {tuple(r) for r in conn.execute(
        "SELECT artist_id, role FROM lib2_track_artists WHERE track_id=?", (track,))}


def _album_credits(conn, album):
    return {tuple(r) for r in conn.execute(
        "SELECT artist_id, role FROM lib2_album_artists WHERE album_id=?", (album,))}


def _snapshot(conn, entity_type, entity_id, source):
    return [r[0] for r in conn.execute(
        "SELECT source_artist_id FROM lib2_provider_credits WHERE entity_type=?"
        " AND entity_id=? AND source=? ORDER BY position", (entity_type, entity_id, source))]


def _matched(conn, w, entity_type="track", service="spotify", pid="sp-otis-1",
             guests=("sp-ye",)):
    """a worker match: the id, then the credits it came back with."""
    entity_id = w[entity_type]
    set_library_v2_match(conn, entity_type, entity_id, service, pid)
    own = "sp-jay" if service == "spotify" else "dz-jay"
    link_credited_artists(conn, entity_type, entity_id, service,
                          [{"id": own}] + [{"id": g} for g in guests])
    conn.commit()


def test_a_rematch_drops_the_old_releases_guests(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    assert (w["kanye"], "featured") in _track_credits(conn, w["track"])

    apply_manual_match(conn, "track", w["track"], "spotify", "sp-otis-2", actor="profile:1")

    assert _track_credits(conn, w["track"]) == {(w["jay"], "primary")}
    assert _snapshot(conn, "track", w["track"], "spotify") == []


def test_a_cleared_match_drops_them_too(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w, entity_type="album")
    assert (w["kanye"], "featured") in _album_credits(conn, w["album"])

    apply_manual_match(conn, "album", w["album"], "spotify", None, actor="profile:1")

    assert _album_credits(conn, w["album"]) == {(w["jay"], "primary")}


def test_the_same_id_again_keeps_them(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    apply_manual_match(conn, "track", w["track"], "spotify", "sp-otis-1", actor="profile:1")
    assert (w["kanye"], "featured") in _track_credits(conn, w["track"])
    assert _snapshot(conn, "track", w["track"], "spotify") == ["sp-jay", "sp-ye"]


def test_another_sources_match_is_left_alone(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    set_library_v2_match(conn, "track", w["track"], "deezer", "dz-otis")
    link_credited_artists(conn, "track", w["track"], "deezer",
                          [{"id": "dz-jay"}, {"id": "dz-ye"}])

    apply_manual_match(conn, "track", w["track"], "spotify", "sp-otis-2", actor="profile:1")

    # deezer still credits kanye on this track
    assert (w["kanye"], "featured") in _track_credits(conn, w["track"])
    assert _snapshot(conn, "track", w["track"], "deezer") == ["dz-jay", "dz-ye"]


@pytest.mark.parametrize("title,track_artist", [
    ("Otis (feat. Kanye West)", None),
    ("Otis", "Jay-Z & Kanye West"),
])
def test_a_guest_the_files_own_tags_name_stays(tmp_path, title, track_artist):
    db, conn, w = _world(tmp_path, title=title, track_artist=track_artist)
    _matched(conn, w, guests=("sp-ye", "sp-frank"))

    apply_manual_match(conn, "track", w["track"], "spotify", "sp-otis-2", actor="profile:1")

    credits = _track_credits(conn, w["track"])
    assert (w["kanye"], "featured") in credits
    assert (w["frank"], "featured") not in credits


def test_an_album_guest_who_is_on_one_of_its_tracks_stays(tmp_path):
    db, conn, w = _world(tmp_path)
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
                 " VALUES(?, ?, 'featured', 1)", (w["track"], w["kanye"]))
    _matched(conn, w, entity_type="album", guests=("sp-ye", "sp-frank"))

    apply_manual_match(conn, "album", w["album"], "spotify", "sp-wtt-2", actor="profile:1")

    credits = _album_credits(conn, w["album"])
    assert (w["kanye"], "featured") in credits
    assert (w["frank"], "featured") not in credits


def test_a_primary_credit_is_never_removed(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    assert drop_source_credits(conn, "track", w["track"], "spotify") == 1
    assert (w["jay"], "primary") in _track_credits(conn, w["track"])


def test_a_worker_relink_drops_only_the_guests_the_new_list_lacks(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w, guests=("sp-ye", "sp-frank"))

    link_credited_artists(conn, "track", w["track"], "spotify",
                          [{"id": "sp-jay"}, {"id": "sp-ye"}])

    credits = _track_credits(conn, w["track"])
    assert (w["kanye"], "featured") in credits
    assert (w["frank"], "featured") not in credits
    assert _snapshot(conn, "track", w["track"], "spotify") == ["sp-jay", "sp-ye"]


def test_unknown_credits_keep_what_is_stored(tmp_path):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    assert link_credited_artists(conn, "track", w["track"], "spotify", None) == 0
    assert (w["kanye"], "featured") in _track_credits(conn, w["track"])


def test_the_new_match_brings_its_own_guests(tmp_path, monkeypatch):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    apply_manual_match(conn, "track", w["track"], "spotify", "sp-otis-2", actor="profile:1")
    conn.commit()
    asked = []
    monkeypatch.setattr(provider_credits, "_fetch_credits",
                        lambda service, entity_type, pid: asked.append(
                            (service, entity_type, pid)) or [{"id": "sp-jay"},
                                                            {"id": "sp-frank"}])

    assert refresh_match_credits(db, "track", w["track"], "spotify", "sp-otis-2") == 1

    assert asked == [("spotify", "track", "sp-otis-2")]
    fresh = db._get_connection()
    assert _track_credits(fresh, w["track"]) == {(w["jay"], "primary"),
                                                 (w["frank"], "featured")}
    fresh.close()


def test_a_fetch_that_lands_after_another_rematch_writes_nothing(tmp_path, monkeypatch):
    db, conn, w = _world(tmp_path)
    set_library_v2_match(conn, "track", w["track"], "spotify", "sp-otis-3")
    conn.commit()
    monkeypatch.setattr(provider_credits, "_fetch_credits",
                        lambda *a: [{"id": "sp-jay"}, {"id": "sp-frank"}])

    assert refresh_match_credits(db, "track", w["track"], "spotify", "sp-otis-2") == 0
    assert _track_credits(conn, w["track"]) == {(w["jay"], "primary")}


def test_a_source_that_cannot_say_writes_nothing(tmp_path, monkeypatch):
    db, conn, w = _world(tmp_path)
    _matched(conn, w)
    monkeypatch.setattr(provider_credits, "_fetch_credits", lambda *a: None)
    assert refresh_match_credits(db, "track", w["track"], "spotify", "sp-otis-1") == 0
    monkeypatch.setattr(provider_credits, "_fetch_credits",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("429")))
    assert refresh_match_credits(db, "track", w["track"], "spotify", "sp-otis-1") == 0
    assert (w["kanye"], "featured") in _track_credits(conn, w["track"])


def test_the_refresh_runs_off_thread_for_credit_sources_only(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_credits, "ONLINE_REFRESH", True)
    calls = []
    monkeypatch.setattr(provider_credits, "refresh_match_credits",
                        lambda *a: calls.append(a))
    thread = schedule_match_credits("db", "tracks", 7, "Spotify", "sp-1")
    thread.join(5)
    assert calls == [("db", "track", 7, "spotify", "sp-1")]
    assert schedule_match_credits("db", "artist", 7, "spotify", "sp-1") is None
    assert schedule_match_credits("db", "track", 7, "musicbrainz", "mb") is None
    assert schedule_match_credits("db", "track", 7, "spotify", None) is None
    monkeypatch.setattr(provider_credits, "ONLINE_REFRESH", False)
    assert schedule_match_credits("db", "track", 7, "spotify", "sp-1") is None


def test_the_spotify_fetch_never_takes_the_fallback_sources_ids(monkeypatch):
    seen = {}

    class _Spotify:
        def get_track_details(self, pid, allow_fallback=True):
            seen["track"] = allow_fallback
            return {"raw_data": {"artists": [{"id": "a"}, {"id": "b"}]}}

        def get_album(self, pid, allow_fallback=True):
            seen["album"] = allow_fallback
            return {"artists": [{"id": "c"}]}

    class _Deezer:
        def get_track_raw(self, pid):
            return {"contributors": [{"id": pid}]}

        def get_album_raw(self, pid):
            return None

    from core.metadata import registry
    monkeypatch.setattr(registry, "get_spotify_client", lambda: _Spotify())
    monkeypatch.setattr(registry, "get_deezer_client", lambda: _Deezer())
    fetch = provider_credits._fetch_credits
    assert fetch("spotify", "track", "x") == [{"id": "a"}, {"id": "b"}]
    assert fetch("spotify", "album", "x") == [{"id": "c"}]
    assert seen == {"track": False, "album": False}
    assert fetch("deezer", "track", "12") == [{"id": 12}]
    assert fetch("deezer", "album", "12") is None
