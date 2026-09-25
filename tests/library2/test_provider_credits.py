"""Every artist a source credits on a match lands on the lib2 junctions
(upstream 3.4.6's collab albums and "appears on", ported to Library v2)."""

from __future__ import annotations

import json

from core.library2.provider_credits import link_credited_artists
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


def _world(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    conn = db._get_connection()
    jay = seed_artist(conn, server_id="jay", name="Jay-Z", server_source="soulsync")
    kanye = seed_artist(conn, server_id="ye", name="Kanye West", server_source="soulsync")
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-jay' WHERE id=?", (jay,))
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-ye', external_ids=? WHERE id=?",
                 (json.dumps({"deezer": "dz-ye"}), kanye))
    album = seed_album(conn, server_id="wtt", title="Watch the Throne", artist_id=jay,
                       server_source="soulsync")
    track = seed_track(conn, server_id="otis", title="Otis", album_id=album, artist_id=jay,
                       server_source="soulsync")
    conn.commit()
    return db, conn, jay, kanye, album, track


def _credits(conn, table, key, entity_id):
    return {tuple(r) for r in conn.execute(
        f"SELECT artist_id, role FROM {table} WHERE {key}=?", (entity_id,))}


def test_a_featured_library_artist_is_credited_on_the_track(tmp_path):
    db, conn, jay, kanye, _album, track = _world(tmp_path)
    added = link_credited_artists(conn, "track", track, "spotify",
                                  [{"id": "sp-jay"}, {"id": "sp-ye"}, {"id": "sp-otis"}])
    assert added == 1
    assert _credits(conn, "lib2_track_artists", "track_id", track) == {
        (jay, "primary"), (kanye, "featured")}


def test_a_collab_album_shows_under_every_album_artist(tmp_path):
    db, conn, jay, kanye, album, _track = _world(tmp_path)
    link_credited_artists(conn, "album", album, "deezer",
                          [{"id": "dz-jay"}, {"id": "dz-ye"}])
    assert (kanye, "featured") in _credits(conn, "lib2_album_artists", "album_id", album)


def test_a_sole_artist_or_an_unknown_source_adds_nothing(tmp_path):
    db, conn, _jay, _kanye, _album, track = _world(tmp_path)
    assert link_credited_artists(conn, "track", track, "spotify", [{"id": "sp-ye"}]) == 0
    assert link_credited_artists(conn, "track", track, "tidal",
                                 [{"id": "a"}, {"id": "b"}]) == 0
