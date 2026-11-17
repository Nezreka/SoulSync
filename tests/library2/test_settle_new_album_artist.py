"""A new download's album artist that reads as a list is settled after the
import, with the writers that exist: the credit writer fetches the tracks'
credits, the provider split resolves every part (#2150 follow-up)."""

from __future__ import annotations

import pytest

import core.library2.provider_credits as pc
from core.library2 import native_enrich as NE
from database.music_database import MusicDatabase


def _album(db, artist, spotify_id=None):
    with db._get_connection() as conn:
        aid = conn.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id) VALUES(?,?,?)",
                           (artist, artist, spotify_id)).lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                             "VALUES(?, 'Album', 'album')", (aid,)).lastrowid
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id) "
                             "VALUES(?, 'Song', 'sp-t')", (album,)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'primary', 0)", (track, aid))
        conn.commit()
    return aid, album, track


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setattr(pc, "ONLINE_REFRESH", True)
    calls = []

    def ensure(database, track_id, services=None, **kw):
        calls.append(track_id)
        with database._get_connection() as conn:
            for pos, name in enumerate(writer.credits):
                conn.execute("INSERT OR REPLACE INTO lib2_provider_credits(entity_type, entity_id,"
                             " source, position, name, source_artist_id) VALUES('track', ?,"
                             " 'spotify', ?, ?, ?)", (track_id, pos, name, f"id{pos}"))
            conn.commit()
        return True
    monkeypatch.setattr(pc, "ensure_track_credits", ensure)
    writer.calls = calls
    writer.credits = []
    return writer


def _resolver(known):
    return lambda name: ({"artist_id": f"sp-{name}", "source": "spotify", "name": name}
                         if name in known else None)


def _primary(db, album):
    with db._get_connection() as conn:
        return conn.execute("SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar "
                            "ON ar.id = al.primary_artist_id WHERE al.id=?", (album,)).fetchone()[0]


def test_a_list_the_provider_credits_apart_is_split_through_the_providers(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia & Toby Fox")
    writer.credits = ["Camellia", "Toby Fox"]
    result = NE.settle_new_album_artist(db, album, resolver=_resolver({"Camellia", "Toby Fox"}))
    assert result and len(result["component_ids"]) == 2
    assert _primary(db, album) == "Camellia"


def test_a_band_the_provider_credits_whole_stays(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Nova & Vale")
    writer.credits = ["Nova & Vale"]
    assert NE.settle_new_album_artist(db, album, resolver=_resolver({"Nova", "Vale"})) is None
    assert _primary(db, album) == "Nova & Vale"


def test_a_band_a_provider_knows_by_its_whole_name_stays_without_credits(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Nova & Vale")
    assert NE.settle_new_album_artist(
        db, album, resolver=_resolver({"Nova & Vale", "Nova", "Vale"})) is None


def test_an_identified_or_plain_artist_is_left_alone(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia & Toby Fox", spotify_id="sp-combined")
    assert NE.settle_new_album_artist(db, album, resolver=_resolver({"Camellia", "Toby Fox"})) is None
    _aid, album2, _t = _album(db, "Camellia")
    assert NE.settle_new_album_artist(db, album2, resolver=_resolver(set())) is None
    assert writer.calls == []        # nothing asked for either


def test_a_part_no_provider_resolves_keeps_the_name(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia & Toby Fox")
    writer.credits = ["Camellia", "Toby Fox"]
    assert NE.settle_new_album_artist(db, album, resolver=_resolver({"Camellia"})) is None
    assert _primary(db, album) == "Camellia & Toby Fox"


def test_offline_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "ONLINE_REFRESH", False)
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia & Toby Fox")
    assert NE.settle_new_album_artist(db, album) is None


def test_parts_that_merely_exist_never_mint_artists_after_a_download(tmp_path, writer):
    # the provider has no credits for the track; "K" and "DA" both resolve
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Kay & Dee")
    assert NE.settle_new_album_artist(db, album, resolver=_resolver({"Kay", "Dee"})) is None
    assert _primary(db, album) == "Kay & Dee"
    with db._get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}
    assert names == {"Kay & Dee"}


def test_a_sure_separator_is_evidence_enough(tmp_path, writer):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, _t = _album(db, "Camellia feat. Toby Fox")
    result = NE.settle_new_album_artist(db, album, resolver=_resolver({"Camellia", "Toby Fox"}))
    assert result and _primary(db, album) == "Camellia"


def test_a_settled_split_hands_the_files_to_retag_and_reorganize(tmp_path, writer, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _aid, album, track = _album(db, "Camellia & Toby Fox")
    writer.credits = ["Camellia", "Toby Fox"]
    asked = []
    import core.repair_worker as rw
    monkeypatch.setattr(rw, "_ACTIVE_WORKER", type("W", (), {
        "request_projection": lambda self, ids, **kw: asked.append(list(ids))})())
    NE.settle_new_album_artist(db, album, resolver=_resolver({"Camellia", "Toby Fox"}))
    assert asked == [[track]]
