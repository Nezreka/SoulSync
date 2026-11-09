"""#2022: Download Discography uses the artist's Quality Profile.

The watchlist row's profile (set under the artist's watchlist rules) and the
Library v2 catalogue's track > album > artist cascade are both honoured; the
app default is only the last resort.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.library2.profile_lookup import assign_quality_profile
from core.quality.artist_profile import ArtistQualityProfile
from tests.lib2_seed import RowDb, album, artist, track

STANDARD, LOSSLESS, SMALL = 1, 2, 3


def _profiles(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS quality_profiles(
        id INTEGER PRIMARY KEY, name TEXT, is_default INTEGER DEFAULT 0)""")
    for pid, name, default in ((STANDARD, "Standard", 1), (LOSSLESS, "Lossless", 0),
                               (SMALL, "Small", 0)):
        conn.execute("INSERT OR IGNORE INTO quality_profiles(id, name, is_default) "
                     "VALUES(?,?,?)", (pid, name, default))
    conn.commit()


def _watchlist(conn, rows):
    conn.execute("DROP TABLE IF EXISTS watchlist_artists")
    conn.execute("""CREATE TABLE watchlist_artists(
        id INTEGER PRIMARY KEY, artist_name TEXT, spotify_artist_id TEXT,
        itunes_artist_id TEXT, deezer_artist_id TEXT, discogs_artist_id TEXT,
        musicbrainz_artist_id TEXT, amazon_artist_id TEXT,
        quality_profile_id INTEGER, profile_id INTEGER DEFAULT 1)""")
    for row in rows:
        conn.execute(
            "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, deezer_artist_id, "
            "quality_profile_id, profile_id) VALUES(?,?,?,?,?)", row)
    conn.commit()


def _resolver(conn, **kw):
    db = RowDb(conn.execute("PRAGMA database_list").fetchone()["file"])
    kw.setdefault("profile_id", 1)
    return ArtistQualityProfile(db, **kw)


def test_watchlisted_artist_not_in_the_library_uses_its_watchlist_profile(imported_conn):
    """the report: watchlisted, lossless under rules > advanced, then
    Download Discography queued everything on the default."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Foxx Band", "sp-foxx", "dz-foxx", LOSSLESS, 1)])

    resolver = _resolver(imported_conn, artist_id="sp-foxx", artist_name="Foxx Band",
                         source="spotify")
    assert resolver.for_release(["alb-1"], [{"id": "t1"}, {"id": "t2"}]) == [LOSSLESS, LOSSLESS]


def test_the_id_need_not_be_in_the_page_sources_namespace(imported_conn):
    """the modal sends a deezer id with source=deezer, or a spotify id over a
    deezer discography: any of the watchlist row's ids finds it."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Foxx Band", "sp-foxx", "dz-foxx", LOSSLESS, 1)])

    assert _resolver(imported_conn, artist_id="dz-foxx", artist_name="Foxx Band",
                     source="deezer").profile_for() == LOSSLESS
    assert _resolver(imported_conn, artist_id="sp-foxx", artist_name="Foxx Band",
                     source="deezer").profile_for() == LOSSLESS


def test_another_profiles_watchlist_row_is_not_mine(imported_conn):
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Foxx Band", "sp-foxx", None, LOSSLESS, 2)])

    resolver = _resolver(imported_conn, artist_id="sp-foxx", artist_name="Foxx Band",
                         source="spotify", profile_id=1)
    assert resolver.profile_for() is None


def test_unwatched_unknown_artist_stays_on_the_default(imported_conn):
    _profiles(imported_conn)
    _watchlist(imported_conn, [])
    assert _resolver(imported_conn, artist_id="sp-x", artist_name="Nobody",
                     source="spotify").profile_for() is None


def test_a_deleted_profile_is_skipped_not_passed_on(imported_conn):
    """add_to_wishlist rejects an unknown id, which would drop the track."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Foxx Band", "sp-foxx", None, 99, 1)])
    assert _resolver(imported_conn, artist_id="sp-foxx", artist_name="Foxx Band",
                     source="spotify").profile_for() is None


def test_a_name_match_never_overrules_a_different_id(imported_conn):
    """two artists with one name: the watched one has another spotify id."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Nirvana", "sp-us", None, LOSSLESS, 1)])
    assert _resolver(imported_conn, artist_id="sp-uk", artist_name="Nirvana",
                     source="spotify").profile_for() is None
    # the name stands in when nothing contradicts it (a deezer page, a
    # watchlist row with only a spotify id)
    assert _resolver(imported_conn, artist_id="dz-1", artist_name="Nirvana",
                     source="deezer").profile_for() == LOSSLESS
    _watchlist(imported_conn, [("Nirvana", "sp-us", None, LOSSLESS, 1),
                               ("Nirvana", "sp-uk2", None, SMALL, 1)])
    # two watched namesakes: no basis to pick one
    assert _resolver(imported_conn, artist_id="sp-other", artist_name="Nirvana",
                     source="spotify").profile_for() is None


def _library_artist(conn):
    artist(conn, "Lib Artist", spotify_id="sp-lib")
    album_id = album(conn, "Lib Artist", "First", spotify_id="alb-first")
    album(conn, "Lib Artist", "Second", spotify_id="alb-second")
    t1 = track(conn, "Lib Artist", "First", "Opener", spotify_id="trk-open")
    conn.commit()
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Lib Artist'").fetchone()[0]
    return artist_id, album_id, t1


def test_library_artist_profile_is_honoured(imported_conn):
    """the Library v2 artist page's profile, with no watchlist row."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [])
    artist_id, _album_id, _t1 = _library_artist(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, LOSSLESS)
    imported_conn.commit()

    resolver = _resolver(imported_conn, artist_id="sp-lib", artist_name="Lib Artist",
                         source="spotify")
    assert resolver.profile_for(album_ids=["alb-second"]) == LOSSLESS


def test_album_and_track_choices_beat_the_artist_level(imported_conn):
    """track > album > artist > default, the app-wide cascade."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Lib Artist", "sp-lib", None, LOSSLESS, 1)])
    artist_id, album_id, t1 = _library_artist(imported_conn)
    assign_quality_profile(imported_conn, "albums", album_id, SMALL)
    imported_conn.commit()

    resolver = _resolver(imported_conn, artist_id="sp-lib", artist_name="Lib Artist",
                         source="spotify")
    # the album's own choice, for every track of it
    assert resolver.for_release(["alb-first"], [{"id": "new-track"}]) == [SMALL]
    # another release falls back to the watchlist row
    assert resolver.for_release(["alb-second"], [{"id": "x"}]) == [LOSSLESS]

    assign_quality_profile(imported_conn, "tracks", t1, STANDARD)
    imported_conn.commit()
    resolver = _resolver(imported_conn, artist_id="sp-lib", artist_name="Lib Artist",
                         source="spotify")
    assert resolver.for_release(["alb-first"], [{"id": "trk-open"}, {"id": "other"}]) == [
        STANDARD, SMALL]


def test_watchlist_row_beats_the_catalogue_artist_level(imported_conn):
    """both are artist-level: the downloader's own watchlist row is what the
    watchlist scan queues this artist's new releases on, so old and new
    releases land on the same profile."""
    _profiles(imported_conn)
    _watchlist(imported_conn, [("Lib Artist", "sp-lib", None, LOSSLESS, 1)])
    artist_id, _album_id, _t1 = _library_artist(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, SMALL)
    imported_conn.commit()

    assert _resolver(imported_conn, artist_id="sp-lib", artist_name="Lib Artist",
                     source="spotify").profile_for(album_ids=["alb-second"]) == LOSSLESS


def test_download_discography_passes_the_resolved_profile():
    """the endpoint hands each track's profile to add_to_wishlist."""
    source = Path("api/artist_detail.py").read_text(encoding="utf-8")
    fn = next(node for node in ast.parse(source).body
              if isinstance(node, ast.FunctionDef) and node.name == "download_discography")
    calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "add_to_wishlist"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("quality_profile_id"), ast.Name)
    assert kw["quality_profile_id"].id == "track_profile"
    body = ast.get_source_segment(source, fn) or ""
    assert "ArtistQualityProfile(" in body
    assert ".for_release(" in body


def test_quality_profile_endpoint_answers_for_the_album_dialog(imported_conn, monkeypatch):
    """the single-album dialog on the artist page preselects this."""
    from flask import Flask

    from api import artist_detail

    _profiles(imported_conn)
    _watchlist(imported_conn, [("Foxx Band", "sp-foxx", None, LOSSLESS, 1)])
    db = RowDb(imported_conn.execute("PRAGMA database_list").fetchone()["file"])
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda *a, **k: db)
    monkeypatch.setattr(artist_detail, "get_current_profile_id", lambda: 1)
    app = Flask(__name__)
    app.register_blueprint(artist_detail.bp)
    client = app.test_client()

    hit = client.get("/api/artist/sp-foxx/quality-profile?name=Foxx%20Band&source=spotify")
    assert hit.get_json() == {"success": True, "quality_profile_id": LOSSLESS}
    miss = client.get("/api/artist/sp-none/quality-profile?name=Nobody&source=spotify")
    assert miss.get_json() == {"success": True, "quality_profile_id": None}
