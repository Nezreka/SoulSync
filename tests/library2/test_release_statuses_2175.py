"""#2175: keep releases whose MusicBrainz status you don't want off the wishlist.

Library v2's side: a re-expansion only wishlists a new release group whose
statuses are allowed, it keeps what MusicBrainz said on the album, and the
Discography Refresh's missing-release findings go by it. A group with no
status, or from a source with no statuses, always gets through.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.release_statuses as RS
import core.settings as settings
from core.library2 import discography as D
from core.library2.artist_settings import (
    ArtistSettingsError,
    get_artist_settings,
    update_artist_settings,
)
from core.library2.schema import ensure_library_v2_schema
from core.repair_jobs.lib2_discography_refresh import (
    Lib2DiscographyRefreshJob,
    _release_included,
)
from tests import lib2_seed


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr("core.library2.artwork.schedule_missing_artwork", lambda *_a, **_k: 0)
    monkeypatch.setattr("core.library2.provider_adapters.fetch_album_tracklist",
                        lambda *_a, **_k: None)


@pytest.fixture()
def cfg(monkeypatch):
    store = {}
    cm = settings.config_manager
    monkeypatch.setattr(cm, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(cm, "set", lambda key, value: store.__setitem__(key, value))
    return store


# -- expansion -----------------------------------------------------------------

def _mb_cards(*releases):
    out = {"albums": [], "eps": [], "singles": [], "success": True, "source": "musicbrainz"}
    for title, group in releases:
        out["albums"].append({"id": group, "release_group_id": group, "title": title,
                              "album_type": "album", "track_count": 10})
    return out


def _monitored_artist(legacy_db, imported_conn, mbid="mb-drake"):
    aid = imported_conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all', "
                 "musicbrainz_id=? WHERE id=?", (mbid, aid))
    conn.commit()
    conn.close()
    return aid


def _expand_twice(legacy_db, aid, monkeypatch, grown, *, source="musicbrainz"):
    first = _mb_cards(("Views", "rg-views"))
    first["source"] = grown["source"] = source
    payloads = iter([first, grown])
    monkeypatch.setattr("core.metadata.discography.get_artist_detail_discography",
                        lambda *_a, **_k: next(payloads))
    D.expand_artist_discography(legacy_db, aid)
    return D.expand_artist_discography(legacy_db, aid)


GROUPS = {
    "rg-views": {"official"},
    "rg-official": {"official", "bootleg"},
    "rg-bootleg": {"bootleg"},
    "rg-promo": {"promotion"},
    "rg-nostatus": set(),
}


def _grown():
    return _mb_cards(("Views", "rg-views"), ("Official One", "rg-official"),
                     ("Bootleg One", "rg-bootleg"), ("Promo One", "rg-promo"),
                     ("No Status One", "rg-nostatus"), ("Unknown One", "rg-unknown"))


def _monitored(conn):
    return {r["title"]: r["monitored"] for r in conn.execute(
        "SELECT title, monitored FROM lib2_albums WHERE origin='discography'")}


def test_a_new_release_group_needs_an_allowed_status(
        legacy_db, imported_conn, monkeypatch, cfg):
    cfg[RS.CONFIG_KEY] = ["official"]
    asked = []
    monkeypatch.setattr(RS, "fetch_group_statuses",
                        lambda mbid, **_k: asked.append(mbid) or GROUPS)
    aid = _monitored_artist(legacy_db, imported_conn)

    _expand_twice(legacy_db, aid, monkeypatch, _grown())

    assert asked and set(asked) == {"mb-drake"}
    assert _monitored(imported_conn) == {
        "Official One": 1,      # one of its releases is official
        "Bootleg One": 0,
        "Promo One": 0,
        "No Status One": 1,     # no status to go by: it gets through
        "Unknown One": 1,       # MusicBrainz said nothing about it
    }
    stored = imported_conn.execute(
        "SELECT release_statuses FROM lib2_albums WHERE title='Bootleg One'").fetchone()[0]
    assert json.loads(stored) == {"group": "rg-bootleg", "statuses": ["bootleg"]}
    assert imported_conn.execute(
        "SELECT release_statuses FROM lib2_albums WHERE title='Unknown One'").fetchone()[0] is None


def test_every_status_allowed_asks_musicbrainz_nothing(
        legacy_db, imported_conn, monkeypatch, cfg):
    monkeypatch.setattr(RS, "fetch_group_statuses",
                        lambda *_a, **_k: pytest.fail("asked MusicBrainz for statuses"))
    aid = _monitored_artist(legacy_db, imported_conn)

    _expand_twice(legacy_db, aid, monkeypatch, _grown())

    assert all(v == 1 for k, v in _monitored(imported_conn).items() if k != "Views")


def test_another_source_has_no_statuses_and_keeps_nothing_out(
        legacy_db, imported_conn, monkeypatch, cfg):
    cfg[RS.CONFIG_KEY] = ["official"]
    monkeypatch.setattr(RS, "fetch_group_statuses",
                        lambda *_a, **_k: pytest.fail("asked MusicBrainz for a spotify catalogue"))
    aid = _monitored_artist(legacy_db, imported_conn)

    _expand_twice(legacy_db, aid, monkeypatch, _grown(), source="spotify")

    assert all(v == 1 for k, v in _monitored(imported_conn).items() if k != "Views")


def test_an_artists_own_statuses_beat_the_librarys(
        legacy_db, imported_conn, monkeypatch, cfg):
    cfg[RS.CONFIG_KEY] = ["official"]
    monkeypatch.setattr(RS, "fetch_group_statuses", lambda *_a, **_k: GROUPS)
    aid = _monitored_artist(legacy_db, imported_conn)
    real = D._artist_content_filters
    monkeypatch.setattr(D, "_artist_content_filters", lambda *a: {
        **real(*a), "release_statuses": ("official", "bootleg")})

    _expand_twice(legacy_db, aid, monkeypatch, _grown())

    monitored = _monitored(imported_conn)
    assert monitored["Bootleg One"] == 1
    assert monitored["Promo One"] == 0


# -- artist settings: inherit or its own ---------------------------------------

WATCHLIST_DDL = """
CREATE TABLE watchlist_artists(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_artist_id TEXT, itunes_artist_id TEXT, deezer_artist_id TEXT,
    discogs_artist_id TEXT, amazon_artist_id TEXT, musicbrainz_artist_id TEXT,
    artist_name TEXT NOT NULL, image_url TEXT,
    include_albums INTEGER DEFAULT 1, include_eps INTEGER DEFAULT 1,
    include_singles INTEGER DEFAULT 1, include_live INTEGER DEFAULT 0,
    include_remixes INTEGER DEFAULT 0, include_acoustic INTEGER DEFAULT 0,
    include_compilations INTEGER DEFAULT 0, include_instrumentals INTEGER DEFAULT 0,
    auto_download INTEGER DEFAULT 1, lookback_days INTEGER,
    preferred_metadata_source TEXT, last_scan_timestamp TEXT, updated_at TEXT,
    allowed_release_statuses TEXT,
    profile_id INTEGER NOT NULL DEFAULT 1
)
"""


def _watched(conn):
    conn.execute(WATCHLIST_DDL)
    conn.execute("INSERT INTO watchlist_artists(spotify_artist_id, artist_name) VALUES('sp1', 'A')")
    conn.commit()
    return conn.execute("SELECT id FROM lib2_artists WHERE spotify_id='sp1'").fetchone()[0]


def test_an_artist_follows_the_library_until_it_picks(imported_conn, cfg):
    cfg[RS.CONFIG_KEY] = ["official", "promotion"]
    aid = _watched(imported_conn)

    got = get_artist_settings(imported_conn, aid)
    assert got["release_statuses"] is None
    assert got["library_release_statuses"] == ["official", "promotion"]
    assert got["effective_release_statuses"] == ["official", "promotion"]

    got = update_artist_settings(imported_conn, aid, {"release_statuses": ["Bootleg", "official"]})
    assert got["release_statuses"] == ["official", "bootleg"]
    assert got["effective_release_statuses"] == ["official", "bootleg"]
    # the library moving doesn't move an artist with its own
    cfg[RS.CONFIG_KEY] = ["official"]
    assert get_artist_settings(imported_conn, aid)["effective_release_statuses"] == [
        "official", "bootleg"]
    assert D._artist_content_filters(imported_conn, aid)["release_statuses"] == (
        "official", "bootleg")

    got = update_artist_settings(imported_conn, aid, {"release_statuses": None})
    assert got["release_statuses"] is None
    assert got["effective_release_statuses"] == ["official"]


def test_a_status_update_leaves_the_release_rules_alone(imported_conn, cfg):
    aid = _watched(imported_conn)
    before = get_artist_settings(imported_conn, aid)
    after = update_artist_settings(imported_conn, aid, {"release_statuses": ["official"]})
    for key in ("include_albums", "include_eps", "include_singles", "follow_defaults"):
        assert after[key] == before[key]


@pytest.mark.parametrize("bad", [[], ["official", "made-up"], "official", 3])
def test_a_bad_status_list_is_refused(imported_conn, cfg, bad):
    aid = _watched(imported_conn)
    with pytest.raises(ArtistSettingsError):
        update_artist_settings(imported_conn, aid, {"release_statuses": bad})


def test_an_artist_on_no_watchlist_goes_by_the_library(imported_conn, cfg):
    cfg[RS.CONFIG_KEY] = ["official"]
    aid = imported_conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]
    assert D._artist_content_filters(imported_conn, aid)["release_statuses"] == ("official",)


# -- the Discography Refresh's missing releases --------------------------------

def _stored(group, statuses):
    return json.dumps({"group": group, "statuses": statuses})


@pytest.mark.parametrize("stored, group, allowed, expected", [
    (_stored("rg", ["bootleg"]), "rg", ("official",), False),
    (_stored("rg", ["bootleg", "official"]), "rg", ("official",), True),
    (_stored("rg", []), "rg", ("official",), True),               # no status on any release
    (None, "rg", ("official",), True),                            # never asked
    (_stored("rg-old", ["bootleg"]), "rg-new", ("official",), True),  # re-matched since
    (_stored("rg", ["bootleg"]), "rg", RS.RELEASE_STATUSES, True),  # every status allowed
])
def test_a_missing_release_goes_by_what_musicbrainz_said(stored, group, allowed, expected):
    album = {"title": "Some Album", "album_type": "album", "track_count": 10,
             "release_statuses": stored, "musicbrainz_release_group_id": group}
    filters = {**D._CONTENT_FILTER_DEFAULTS, "release_statuses": allowed}
    assert _release_included(album, filters) is expected


def test_refresh_findings_leave_out_a_bootleg(tmp_path, cfg):
    cfg[RS.CONFIG_KEY] = ["official"]
    db = lib2_seed.RowDb(str(tmp_path / "lib2.db"))
    with db._get_connection() as conn:
        ensure_library_v2_schema(conn)
        lib2_seed.track(conn, "Band", "Owned Album", "Song")
        for title, group, statuses in (("Studio Two", "rg-2", ["official"]),
                                       ("Live Tape", "rg-3", ["bootleg"]),
                                       ("Never Asked", "rg-4", None)):
            lib2_seed.album(conn, "Band", title, origin="discography", album_type="album",
                            track_count=10, monitored=0, musicbrainz_release_group_id=group,
                            release_statuses=_stored(group, statuses) if statuses else None)
        conn.commit()
        artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Band'").fetchone()[0]

    created = []
    job = Lib2DiscographyRefreshJob()
    context = SimpleNamespace(db=db, create_finding=lambda **kw: created.append(kw) or True)
    job._create_backfill_findings(context, artist_id, job._settings(
        SimpleNamespace(config_manager=None)), SimpleNamespace(findings_created=0))

    assert {f["details"]["lib2_album_id"] and f["title"] for f in created} == {
        "Missing release: Studio Two — Band", "Missing release: Never Asked — Band"}
