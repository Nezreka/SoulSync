"""A track's own credits decide how an artist string splits (#2150).

The kernel (core/matching/artist_credit.py) only reads what the library holds;
fetching is the credit writer's job (provider_credits.ensure_track_credits),
which stores what it learns so nobody asks twice.
"""

from __future__ import annotations

import pytest

import core.library2.provider_credits as pc
from core.matching.artist_credit import (
    credit_verdict, credits_cover, decide_credit, stored_track_credits)
from database.music_database import MusicDatabase


# ── credit_verdict: what one credit list says ───────────────────────────────

@pytest.mark.parametrize("name", [
    "Above & Beyond", "AC/DC", "K/DA", "Simon & Garfunkel", "Earth, Wind & Fire",
    "Tyler, The Creator", "Tom Petty & The Heartbreakers", "Crosby, Stills, Nash & Young",
])
def test_a_list_that_credits_the_string_as_one_artist_keeps_it_whole(name):
    assert credit_verdict(name, [("spotify credits", [name])]) == ("whole", [name], "spotify credits")


def test_a_band_inside_a_longer_credit_is_one_part():
    verdict = credit_verdict("Above & Beyond & Zoë Johnston",
                             [("spotify credits", ["Zoë Johnston", "Above & Beyond"])])
    assert verdict == ("split", ["Above & Beyond", "Zoë Johnston"], "spotify credits")


@pytest.mark.parametrize("name,credits,parts", [
    ("Camellia, Toby Fox", ["Camellia", "Toby Fox"], ["Camellia", "Toby Fox"]),
    ("K/DA; League Of Legends; Mimi Webb", ["K/DA", "League Of Legends", "Mimi Webb"],
     ["K/DA", "League Of Legends", "Mimi Webb"]),
    ("A / B / C", ["C", "A", "B"], ["A", "B", "C"]),
    ("Drake feat. Wizkid & Kyla", ["Drake", "Wizkid", "Kyla"], ["Drake", "Wizkid", "Kyla"]),
    ("  Camellia ,  TOBY FOX ", ["camellia", "Toby Fox"], ["camellia", "Toby Fox"]),
    ("Okayracer, aldrch & poptropicaslutz!", ["Okayracer", "aldrch", "poptropicaslutz!"],
     ["Okayracer", "aldrch", "poptropicaslutz!"]),
    ([{"name": "x"}][0]["name"] + " & y", [{"name": "x"}, {"name": "y"}], ["x", "y"]),
])
def test_names_that_make_up_the_string_are_the_split(name, credits, parts):
    assert credit_verdict(name, [("x", credits)]) == ("split", parts, "x")


@pytest.mark.parametrize("credits", [
    ["Camellia"],                       # a list naming only the lead
    ["Camellia", "Someone Else"],       # Toby Fox is not credited
    [],
    ["Camellia", "Toby Fox", "Extra"],  # Extra is not in the string
])
def test_a_list_that_does_not_account_for_the_string_says_nothing(credits):
    assert credit_verdict("Camellia, Toby Fox", [("x", credits)]) is None


def test_the_first_list_that_decides_wins_and_later_ones_are_not_read():
    def lists():
        yield "deezer credits", ["Above & Beyond"]
        raise AssertionError("read past the deciding list")
    assert credit_verdict("Above & Beyond", lists())[0] == "whole"
    lists2 = [("deezer credits", ["Camellia"]), ("spotify credits", ["Camellia", "Toby Fox"])]
    assert credit_verdict("Camellia, Toby Fox", lists2)[2] == "spotify credits"


def test_catalogue_credits_only_ever_split():
    # the catalogue crediting "Camellia, Toby Fox" as one artist is the dummy
    assert credit_verdict("Camellia, Toby Fox",
                          [("catalogue:split", ["Camellia, Toby Fox"])]) is None
    assert credit_verdict("Camellia, Toby Fox",
                          [("catalogue:split", ["Camellia", "Toby Fox"])])[2] == "catalogue"


def test_only_separators_may_sit_between_the_names():
    assert credits_cover("A feat. B", ["A", "B"]) == ["A", "B"]
    assert credits_cover("A and then B", ["A", "B"]) is None
    assert credits_cover("", ["A", "B"]) is None


def test_empty_input_never_decides():
    assert credit_verdict("", [("x", ["", None])]) is None
    assert credit_verdict("A & B", [("x", None)]) is None


# ── stored_track_credits: the library's own knowledge ──────────────────────

def _track(conn, credits=("Above", "Beyond"), spotify_id=None):
    ids = [conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)", (n, n)).lastrowid
           for n in credits]
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                         "VALUES(?, 'Group Therapy', 'album')", (ids[0],)).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id) "
                         "VALUES(?, 'Sun & Moon', ?)", (album, spotify_id)).lastrowid
    for pos, aid in enumerate(ids):
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'primary', ?)", (track, aid, pos))
    return track


def test_stored_credits_come_provider_first_then_the_catalogue(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn)
        for source in ("deezer", "spotify"):
            conn.execute("INSERT INTO lib2_provider_credits(entity_type, entity_id, source, "
                         "position, name, source_artist_id) VALUES('track', ?, ?, 0, "
                         "'Above & Beyond', 'x')", (track, source))
        conn.commit()
        lists = stored_track_credits(conn, track)
    assert lists == [("spotify credits", ["Above & Beyond"]),
                     ("deezer credits", ["Above & Beyond"]),
                     ("catalogue:split", ["Above", "Beyond"])]
    assert decide_credit("Above & Beyond", evidence=lists).via == "spotify credits"


def test_a_recorded_one_artist_answer_is_read_for_the_tracks_current_id(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, spotify_id="sp-track-1")
        pc._record_check(conn, "spotify", "sp-track-1", [{"name": "Nova & Vale", "id": "a"}])
        pc._record_check(conn, "deezer", "dz-other", [{"name": "Someone"}])  # not this track's id
        conn.commit()
        lists = stored_track_credits(conn, track)
    assert ("spotify credits", ["Nova & Vale"]) in lists
    assert not any(via == "deezer credits" for via, _n in lists)


def test_stored_credits_never_raise():
    class _Broken:
        def execute(self, *a):
            raise RuntimeError("no such table")
    assert stored_track_credits(_Broken(), 1) == []


# ── ensure_track_credits: the scoped writer ────────────────────────────────

@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(pc, "ONLINE_REFRESH", True)
    fetched = []

    def fetch(service, entity_type, provider_id):
        fetched.append((service, provider_id))
        return online.answers.get((service, provider_id))
    monkeypatch.setattr(pc, "_fetch_credits", fetch)
    online.answers = {}
    online.fetched = fetched
    return online


def test_the_writer_stores_a_fetched_list_as_the_snapshot_and_reads_back(tmp_path, online):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, credits=("Camellia, Toby Fox",), spotify_id="sp1")
        conn.commit()
    online.answers[("spotify", "sp1")] = [{"name": "Camellia", "id": "c"},
                                          {"name": "Toby Fox", "id": "t"}]
    assert pc.ensure_track_credits(db, track, {"spotify"}) is True
    with db._get_connection() as conn:
        lists = stored_track_credits(conn, track)
    assert lists[0] == ("spotify credits", ["Camellia", "Toby Fox"])
    assert decide_credit("Camellia, Toby Fox", evidence=lists).names == ("Camellia", "Toby Fox")


def test_the_writer_records_a_one_artist_answer_and_never_asks_twice(tmp_path, online):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, credits=("Nova", "Vale"), spotify_id="sp1")
        conn.commit()
    online.answers[("spotify", "sp1")] = [{"name": "Nova & Vale", "id": "nv"}]
    pc.ensure_track_credits(db, track, {"spotify"})
    pc.ensure_track_credits(db, track, {"spotify"})
    assert online.fetched == [("spotify", "sp1")]
    with db._get_connection() as conn:
        lists = stored_track_credits(conn, track)
    assert decide_credit("Nova & Vale", evidence=lists).names == ("Nova & Vale",)


def test_an_unreachable_provider_records_nothing_and_is_asked_again(tmp_path, online):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, spotify_id="sp1")
        conn.commit()
    assert pc.ensure_track_credits(db, track, {"spotify"}) is False   # answers: None
    pc.ensure_track_credits(db, track, {"spotify"})
    assert len(online.fetched) == 2


def test_a_track_without_an_id_is_matched_by_the_enrichment_first(tmp_path, online, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn)
        conn.commit()
    import core.library2.native_enrich as ne

    def match(conn, entity_type, entity_id, service, **kw):
        conn.execute("UPDATE lib2_tracks SET spotify_id='sp-new' WHERE id=?", (entity_id,))
        return {"success": True, "provider_id": "sp-new", "source": service}
    monkeypatch.setattr(ne, "enrich_native_entity_for_service", match)
    online.answers[("spotify", "sp-new")] = [{"name": "Above & Beyond", "id": "ab"}]
    pc.ensure_track_credits(db, track, {"spotify"})
    assert online.fetched == [("spotify", "sp-new")]


def test_a_service_left_out_is_not_asked(tmp_path, online):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = _track(conn, spotify_id="sp1")
        conn.commit()
    pc.ensure_track_credits(db, track, {"deezer"})
    assert online.fetched == []


def test_the_writer_is_off_in_tests_unless_turned_on(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "ONLINE_REFRESH", False)
    assert pc.ensure_track_credits(object(), 1) is False


@pytest.mark.parametrize('credit,artists', [('AB', ['A', 'B']),
                                          ('A BC', ['A', 'BC']),
                                          ('NovaVale', ['Nova', 'Vale'])])
def test_provider_names_need_an_actual_separator_between_them(credit, artists):
    assert credits_cover(credit, artists) is None


def test_context_never_overrides_a_provider_whole_act():
    assert decide_credit('Drake feat. Rihanna', known=['Drake'],
                         evidence=[('spotify credits', ['Drake feat. Rihanna'])]).names == (
        'Drake feat. Rihanna',)


def test_credit_writer_respects_an_existing_album_metadata_lock(tmp_path, online):
    from core.library2 import locks

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        track = _track(conn, spotify_id='sp1')
        album = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (track,)).fetchone()[0]
        locks.set_lock(conn, 'album', album, ['metadata'])
        conn.commit()
    online.answers[('spotify', 'sp1')] = [{'name': 'Nova & Vale', 'id': 'nv'}]
    with locks.automation():
        assert pc.ensure_track_credits(db, track, {'spotify'}) is False
    assert online.fetched == []


def test_credit_writer_rechecks_lock_after_the_provider_returns(tmp_path, online, monkeypatch):
    from core.library2 import locks

    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        track = _track(conn, spotify_id='sp1')
        album = conn.execute('SELECT album_id FROM lib2_tracks WHERE id=?', (track,)).fetchone()[0]
        conn.commit()

    def fetch(*args):
        with db._get_connection() as conn:
            locks.set_lock(conn, 'album', album, ['metadata'])
            conn.commit()
        return [{'name': 'Nova & Vale', 'id': 'nv'}]

    monkeypatch.setattr(pc, '_fetch_credits', fetch)
    with locks.automation():
        assert pc.ensure_track_credits(db, track, {'spotify'}) is False
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lib2_credit_checks').fetchone()[0] == 0
