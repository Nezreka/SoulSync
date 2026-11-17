"""Every source whose track lookup gives a real artist list is a credit source:
Spotify, Deezer, MusicBrainz, Tidal, Discogs, JioSaavn. Each client reads its
own format into one shape, ``[{"id", "name"}]`` in credit order; the credit
writer stores it, a one-artist list too."""

from __future__ import annotations

import json

import pytest

import core.library2.provider_credits as pc
from core.discogs_client import DiscogsClient
from core.jiosaavn_client import JioSaavnClient
from core.matching.artist_credit import decide_credit, stored_track_credits
from core.musicbrainz_client import MusicBrainzClient
from core.tidal_client import TidalClient
from database.music_database import MusicDatabase


# ── each client reads its own format ─────────────────────────────────────────

def _mb(recording=None, isrc=()):
    client = MusicBrainzClient.__new__(MusicBrainzClient)
    client.get_recording = lambda mbid, includes=None, raise_on_error=False: recording
    client.lookup_recordings_by_isrc = lambda code, raise_on_error=False: list(isrc)
    return client


def _credit(*names):
    return [{"name": n, "joinphrase": " & ", "artist": {"id": f"mb-{n}", "name": n}}
            for n in names]


def test_musicbrainz_reads_the_recordings_artist_credit():
    client = _mb({"id": "rec", "artist-credit": _credit("ODESZA", "RY X")})
    assert client.track_artist_credits("rec") == [
        {"id": "mb-ODESZA", "name": "ODESZA"}, {"id": "mb-RY X", "name": "RY X"}]
    assert _mb(None).track_artist_credits("rec") is None
    assert _mb({"id": "rec"}).track_artist_credits("") is None


def test_musicbrainz_names_the_credit_as_printed():
    credit = [{"name": "Beyoncé", "artist": {"id": "b", "name": "Beyoncé Knowles"}}]
    assert _mb({"artist-credit": credit}).track_artist_credits("r") == [
        {"id": "b", "name": "Beyoncé"}]


def test_an_isrc_names_the_recording_exactly_unless_its_recordings_disagree():
    same = [{"id": "r1", "artist-credit": _credit("ODESZA", "RY X")},
            {"id": "r2", "artist-credit": _credit("ODESZA", "RY X")}]
    rid, credits = _mb(isrc=same).track_artist_credits_by_isrc("USX9P1700001")
    assert rid == "r1" and [c["name"] for c in credits] == ["ODESZA", "RY X"]
    differ = [same[0], {"id": "r3", "artist-credit": _credit("ODESZA")}]
    assert _mb(isrc=differ).track_artist_credits_by_isrc("USX9P1700001") is None
    assert _mb(isrc=[]).track_artist_credits_by_isrc("USX9P1700001") is None


class _Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _tidal(status, data):
    client = TidalClient.__new__(TidalClient)
    client.base_url = "https://openapi.tidal.test/v2"
    client._ensure_valid_token = lambda: True
    asked = []

    class _Session:
        def get(self, url, params=None, headers=None, timeout=None):
            asked.append(params)
            return _Response(status, data)
    client.session = _Session()
    client.asked = asked
    return client


def test_tidal_reads_the_artists_relationship_in_order():
    data = {"data": {"id": "1", "relationships": {"artists": {"data": [
        {"id": "20", "type": "artists"}, {"id": "10", "type": "artists"}]}}},
        "included": [{"id": "10", "type": "artists", "attributes": {"name": "RY X"}},
                     {"id": "20", "type": "artists", "attributes": {"name": "ODESZA"}},
                     {"id": "99", "type": "albums", "attributes": {"name": "A Moment Apart"}}]}
    client = _tidal(200, data)
    assert client.track_artist_credits("1") == [{"id": "20", "name": "ODESZA"},
                                                {"id": "10", "name": "RY X"}]
    assert client.asked[0]["include"] == "artists"
    assert _tidal(404, {}).track_artist_credits("1") is None


def _discogs(release):
    client = DiscogsClient.__new__(DiscogsClient)
    client.get_release = lambda release_id: release
    return client


def test_discogs_reads_the_tracks_own_artists_else_the_releases():
    release = {
        "artists": [{"id": 1, "name": "Above & Beyond"}],
        "tracklist": [
            {"type_": "heading", "title": "Side A"},
            {"type_": "track", "title": "Sun & Moon"},
            {"type_": "track", "title": "Thing Called Love",
             "artists": [{"id": 1, "name": "Above & Beyond", "join": "feat."},
                         {"id": 2, "name": "Richard Bedford (2)", "anv": ""}]},
        ]}
    client = _discogs(release)
    # headings are not tracks: _t1 is "Sun & Moon"
    assert client.track_artist_credits("r123_t1") == [{"id": "1", "name": "Above & Beyond"}]
    assert client.track_artist_credits("123_t2") == [
        {"id": "1", "name": "Above & Beyond"}, {"id": "2", "name": "Richard Bedford"}]
    assert client.track_artist_credits("r123_t3") is None
    assert client.track_artist_credits("m123_t1") is None          # a master, not a release
    assert _discogs(None).track_artist_credits("r123_t1") is None


def test_discogs_prints_the_credited_name_variation():
    release = {"artists": [{"id": 7, "name": "Prince", "anv": "The Artist*"}],
               "tracklist": [{"type_": "track", "title": "X"}]}
    assert _discogs(release).track_artist_credits("r1_t1") == [{"id": "7", "name": "The Artist"}]


def test_jiosaavn_reads_primary_then_featured_artists():
    client = JioSaavnClient.__new__(JioSaavnClient)
    client._get_json = lambda path, params=None: {"data": [{"artists": {
        "primary": [{"id": "p1", "name": "Vishal &amp; Shekhar"}],
        "featured": [{"id": "f1", "name": "Shilpa Rao"}, {"id": "p1", "name": "dup"}]}}]}
    assert client.track_artist_credits("s1") == [{"id": "p1", "name": "Vishal & Shekhar"},
                                                 {"id": "f1", "name": "Shilpa Rao"}]
    client._get_json = lambda path, params=None: {"data": []}
    assert client.track_artist_credits("s1") is None


# ── which sources are asked ──────────────────────────────────────────────────

def test_only_configured_sources_that_give_a_list_are_asked():
    assert pc.credit_services(["spotify", "itunes", "musicbrainz", "amazon", "qobuz"]) == {
        "spotify", "musicbrainz"}
    assert pc.credit_services([]) == set()


# ── the writer ───────────────────────────────────────────────────────────────

def _track(conn, credits=("ODESZA; RY X",), isrc=None, **ids):
    artists = [conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)",
                            (n, n)).lastrowid for n in credits]
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                         "VALUES(?, 'A Moment Apart', 'album')", (artists[0],)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, isrc, musicbrainz_id, external_ids) "
        "VALUES(?, 'Corners Of The Earth', ?, ?, ?)",
        (album, isrc, ids.pop("musicbrainz", None), json.dumps(ids))).lastrowid
    for position, artist in enumerate(artists):
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'primary', ?)", (track, artist, position))
    return track


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(pc, "ONLINE_REFRESH", True)
    fetched = []

    def fetch(service, entity_type, provider_id):
        fetched.append((service, provider_id))
        return online.answers.get((service, provider_id))
    monkeypatch.setattr(pc, "_fetch_credits", fetch)
    online.answers, online.fetched = {}, fetched
    return online


def test_a_one_artist_list_is_kept_and_adds_no_credit(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, credits=("Above & Beyond",))
        assert pc.link_credited_artists(conn, "track", track, "musicbrainz",
                                        [{"id": "mb-ab", "name": "Above & Beyond"}]) == 0
        lists = stored_track_credits(conn, track)
        rows = conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE track_id=?",
                            (track,)).fetchone()[0]
    assert ("musicbrainz credits", ["Above & Beyond"]) in lists
    assert decide_credit("Above & Beyond", evidence=lists).via == "musicbrainz credits"
    assert rows == 1


def test_a_one_artist_list_materializes_nothing_when_its_artist_gets_the_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, credits=("ODESZA; RY X",))
        pc.link_credited_artists(conn, "track", track, "tidal", [{"id": "t-od", "name": "ODESZA"}])
        odesza = conn.execute("INSERT INTO lib2_artists(name, sort_name, external_ids) "
                              "VALUES('ODESZA', 'ODESZA', ?)",
                              (json.dumps({"tidal": "t-od"}),)).lastrowid
        assert pc.materialize_artist_credits(conn, odesza) == 0


def test_a_musicbrainz_list_links_library_artists_by_their_mbid(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, credits=("ODESZA",))
        ry = conn.execute("INSERT INTO lib2_artists(name, sort_name, musicbrainz_id) "
                          "VALUES('RY X', 'RY X', 'mb-ry')").lastrowid
        assert pc.link_credited_artists(conn, "track", track, "musicbrainz", [
            {"id": "mb-od", "name": "ODESZA"}, {"id": "mb-ry", "name": "RY X"}]) == 1
        role = conn.execute("SELECT role FROM lib2_track_artists WHERE track_id=? AND artist_id=?",
                            (track, ry)).fetchone()[0]
    assert role == "featured"


def test_musicbrainz_finds_a_track_by_its_isrc_without_a_text_search(tmp_path, online,
                                                                      monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, isrc="USX9P1700001")
        conn.commit()
    searched = []
    import core.library2.native_enrich as ne
    monkeypatch.setattr(ne, "enrich_native_entity_for_service",
                        lambda *a, **kw: searched.append(kw) or {"success": False})
    client = _mb(isrc=[{"id": "rec-1", "artist-credit": _credit("ODESZA", "RY X")}])
    monkeypatch.setattr(pc, "_credit_client", lambda service: client)
    assert pc.ensure_track_credits(db, track, {"musicbrainz"}) is True
    assert searched == [] and online.fetched == []
    with db._get_connection() as conn:
        lists = stored_track_credits(conn, track)
        checked = conn.execute("SELECT provider_id FROM lib2_credit_checks "
                               "WHERE source='musicbrainz'").fetchone()[0]
    assert lists[0] == ("musicbrainz credits", ["ODESZA", "RY X"])
    assert checked == "rec-1"
    # stored: not asked again
    monkeypatch.setattr(pc, "_credit_client", lambda service: pytest.fail("asked again"))
    assert pc.ensure_track_credits(db, track, {"musicbrainz"}) is False


def test_a_track_the_combined_name_finds_nowhere_is_looked_up_under_its_parts(
        tmp_path, online, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn)
        conn.commit()
    import core.library2.native_enrich as ne
    tried = []

    def match(conn, entity_type, entity_id, service, artist_name=None, **kw):
        tried.append(artist_name)
        if artist_name != "ODESZA":
            return {"success": False, "reason": "not_found"}
        conn.execute("UPDATE lib2_tracks SET spotify_id='sp-1' WHERE id=?", (entity_id,))
        return {"success": True, "source": service}
    monkeypatch.setattr(ne, "enrich_native_entity_for_service", match)
    online.answers[("spotify", "sp-1")] = [{"id": "od", "name": "ODESZA"},
                                           {"id": "ry", "name": "RY X"}]
    assert pc.ensure_track_credits(db, track, {"spotify"},
                                   artist_hints=["ODESZA", "RY X"]) is True
    assert tried == [None, "ODESZA"]          # the album's artist first, then the parts
    with db._get_connection() as conn:
        assert stored_track_credits(conn, track)[0] == ("spotify credits", ["ODESZA", "RY X"])


@pytest.mark.parametrize("service", ["musicbrainz", "discogs", "jiosaavn"])
def test_every_credit_source_answers_through_a_client_that_can(service):
    # the registry's MusicBrainz client is a search facade without the
    # lookups: the credit list must come from the client it wraps
    client = pc._credit_client(service)
    assert callable(getattr(client, "track_artist_credits", None))
    if service == "musicbrainz":
        assert callable(getattr(client, "track_artist_credits_by_isrc", None))
        assert callable(getattr(client, "get_release", None))


def test_tidal_answers_through_the_signed_in_worker_client(monkeypatch):
    from core.library import service_search
    monkeypatch.setattr(service_search, "tidal_enrichment_worker", None)
    assert pc._credit_client("tidal") is None
    client = TidalClient.__new__(TidalClient)
    monkeypatch.setattr(service_search, "tidal_enrichment_worker",
                        type("W", (), {"client": client})())
    assert pc._credit_client("tidal") is client
