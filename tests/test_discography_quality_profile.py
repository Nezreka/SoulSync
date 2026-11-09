"""#2022: Download Discography must queue under the watchlist artist's
quality profile.

Foxxify (Discord): add an artist to the Watchlist, set its quality profile
to a lossless profile in the panel (Rules > Advanced — "Every release this
artist queues uses it"), then View Discography > Download Discography. The
discography queued at the DEFAULT profile instead: the watchlist scanner
hands the artist's profile to `add_to_wishlist` (SYNC-01), but the
discography endpoint never asked for it, so its rows resolved the app-wide
default in `_resolve_quality_profile_id`.
"""

import json
from types import SimpleNamespace

import pytest
from flask import Flask

from core.metadata import discography_filters as df


def _track(tid, title, album_id, album_title):
    return SimpleNamespace(id=tid, title=title, album_id=album_id,
                           album_title=album_title, artist_name="Yellowcard")


LIBRARY_TRACKS = [_track(1, "Ocean Avenue", 10, "Ocean Avenue")]


class _BaseDB:
    """Nothing owned, so every discography track reaches the wishlist."""

    def __init__(self):
        self.wishlist_kwargs = []
        self.lookup_calls = []

    def get_candidate_albums_for_artist(self, artist, server_source=None):
        return [SimpleNamespace(id=10, title="Ocean Avenue")]

    def get_candidate_tracks_for_albums(self, album_ids):
        return [t for t in LIBRARY_TRACKS if t.album_id in album_ids]

    def check_album_exists_with_completeness(self, title, artist, **kw):
        return None, 0.0, 0, 0, False, []

    def check_track_exists(self, title, artist, **kw):
        return None, 0.0

    def add_to_wishlist(self, spotify_track_data, **kw):
        self.wishlist_kwargs.append(kw)
        return True


class _FakeDB(_BaseDB):
    """`watchlist_profile` is what the watchlist lookup answers."""

    watchlist_profile = 7
    lookup_raises = False
    fail_first_lookup = False
    profile_by_name = None

    def get_watchlist_artist_quality_profile_id(self, artist_id, profile_id=1,
                                                artist_name=None, source=None):
        self.lookup_calls.append(
            {"artist_id": artist_id, "profile_id": profile_id,
             "artist_name": artist_name, "source": source})
        if self.lookup_raises or (
                self.fail_first_lookup and len(self.lookup_calls) == 1):
            raise RuntimeError("db busy")
        if self.profile_by_name is not None:
            return self.profile_by_name.get(artist_name)
        return self.watchlist_profile


class _FakeDBNoLookup(_BaseDB):
    """A duck-typed DB without the lookup: queueing must not break."""


RELEASES = {
    "rel-album": ({"name": "Ocean Avenue", "album_type": "album",
                   "release_date": "2003-07-22"},
                  ["Ocean Avenue", "Breathing"], "Yellowcard"),
    "rel-lights": ({"name": "Lights and Sounds", "album_type": "album",
                    "release_date": "2006-01-24"},
                   ["Lights and Sounds"], "Yellowcard"),
    "rel-other": ({"name": "Other Album", "album_type": "album",
                   "release_date": "2020-01-01"},
                  ["Other Song"], "Other Artist"),
}


@pytest.fixture
def run(monkeypatch):
    from api import artist_detail as ad
    import database.music_database as mdb
    from core.metadata import album_tracks

    def go(db, artist_id="dz-42", albums=None, envelope_name="Yellowcard",
           source="deezer", legacy_ids=None):
        monkeypatch.setattr(mdb, "MusicDatabase", lambda: db)
        monkeypatch.setattr(ad, "get_current_profile_id", lambda: 1,
                            raising=False)

        def fake_tracks(album_id, artist_name="", album_name="",
                        source_override=None):
            album, names, artist = RELEASES[album_id]
            return {"success": True, "source": "deezer",
                    "album": dict(album, id=album_id,
                                  artists=[{"name": artist}]),
                    "tracks": [{"id": f"{album_id}-{i}", "name": n,
                                "artists": [{"name": artist}],
                                "track_number": i + 1}
                               for i, n in enumerate(names)]}

        monkeypatch.setattr(album_tracks, "get_artist_album_tracks",
                            fake_tracks)
        monkeypatch.setattr(df, "load_global_content_filter_settings",
                            lambda _cm: {}, raising=False)

        app = Flask(__name__)
        app.register_blueprint(ad.bp)
        client = app.test_client()
        body = {"artist_name": envelope_name}
        if legacy_ids is not None:
            body["album_ids"] = legacy_ids
        else:
            body["albums"] = albums or [{"id": "rel-album",
                                          "name": "Ocean Avenue",
                                          "source": "deezer"}]
        if source is not None:
            body["source"] = source
        resp = client.post(f"/api/artist/{artist_id}/download-discography",
                           json=body)
        lines = [json.loads(line)
                 for line in resp.get_data(as_text=True).splitlines()
                 if line.strip()]
        return lines

    return go


def test_the_watchlist_artists_profile_reaches_every_wishlist_row(run):
    db = _FakeDB()
    lines = run(db)
    assert lines[-1]["total_added"] == 2
    assert db.lookup_calls == [
        {"artist_id": "dz-42", "profile_id": 1, "artist_name": "Yellowcard",
         "source": "deezer"}]
    assert db.wishlist_kwargs
    assert all(kw["quality_profile_id"] == 7 for kw in db.wishlist_kwargs)
    # the user profile is a different namespace and must not be confused
    assert all(kw["profile_id"] == 1 for kw in db.wishlist_kwargs)


def test_no_watchlist_profile_leaves_the_default_resolution_alone(run):
    db = _FakeDB()
    db.watchlist_profile = None
    lines = run(db)
    assert lines[-1]["total_added"] == 2
    assert all(kw["quality_profile_id"] is None for kw in db.wishlist_kwargs)


def test_a_failing_lookup_never_breaks_queueing(run):
    db = _FakeDB()
    db.lookup_raises = True
    lines = run(db)
    assert lines[-1]["total_added"] == 2
    assert all(kw["quality_profile_id"] is None for kw in db.wishlist_kwargs)


def test_a_db_without_the_lookup_still_queues_at_the_default(run):
    db = _FakeDBNoLookup()
    lines = run(db)
    assert lines[-1]["total_added"] == 2
    assert all(kw["quality_profile_id"] is None for kw in db.wishlist_kwargs)


def test_a_failed_lookup_retries_on_the_same_artists_next_album(run):
    """Resolution happens once per album and failures are not cached:
    a transient error on the artist's first album queues it at the
    default, and the SAME artist's next album retries and gets the
    profile — instead of one warning (and one wrong queue) per track."""
    db = _FakeDB()
    db.fail_first_lookup = True
    lines = run(
        db, albums=[
            {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
             "artist_name": "Yellowcard"},
            {"id": "rel-lights", "name": "Lights and Sounds",
             "source": "deezer", "artist_name": "Yellowcard"},
        ])
    assert lines[-1]["total_added"] == 3
    assert len(db.lookup_calls) == 2
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [
        None, None, 7]


def test_a_successful_lookup_is_cached_per_artist(run):
    """Two albums by the same artist resolve ONCE; the second album
    reuses the answer instead of asking the DB again."""
    db = _FakeDB()
    run(db, albums=[
        {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
         "artist_name": "Yellowcard"},
        {"id": "rel-lights", "name": "Lights and Sounds",
         "source": "deezer", "artist_name": "Yellowcard"},
    ])
    assert len(db.lookup_calls) == 1
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [
        7, 7, 7]


def test_the_envelope_lookup_is_labelled_with_the_page_source(run):
    """A gap-fill album from another source streams first: the URL id
    still gets looked up under the PAGE's source (its own namespace),
    never relabelled by whichever album happened to come first."""
    db = _FakeDB()
    run(db, albums=[
        {"id": "rel-album", "name": "Ocean Avenue", "source": "spotify",
         "artist_name": "Yellowcard"},
        {"id": "rel-lights", "name": "Lights and Sounds",
         "source": "deezer", "artist_name": "Yellowcard"},
    ])
    assert db.lookup_calls[0]["artist_id"] == "dz-42"
    assert db.lookup_calls[0]["source"] == "deezer"
    assert len(db.lookup_calls) == 1


def test_an_album_artist_differing_only_in_case_is_the_page_artist(run):
    """An album whose artist_name is the page artist's name in another
    case still counts as the page artist and takes the id lookup."""
    db = _FakeDB()
    run(db, albums=[
        {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
         "artist_name": "yellowcard"}])
    assert db.lookup_calls[0]["artist_id"] == "dz-42"


def test_a_sourceless_playlist_explorer_payload_still_resolves(run):
    """The playlist explorer sends no source anywhere — not on the
    envelope, not on the albums — just the provider id in the URL."""
    db = _FakeDB()
    lines = run(db, source=None, albums=[
        {"id": "rel-album", "name": "Ocean Avenue",
         "artist_name": "Yellowcard"}])
    assert lines[-1]["total_added"] == 2
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [7, 7]


def test_per_album_sources_without_an_envelope_source(run):
    """Some payloads name a source per album but none on the envelope;
    the URL id still resolves (by id + name) and the profile applies."""
    db = _FakeDB()
    lines = run(db, source=None, albums=[
        {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
         "artist_name": "Yellowcard"}])
    assert lines[-1]["total_added"] == 2
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [7, 7]


def test_the_legacy_album_ids_payload_resolves_the_envelope_artist(run):
    db = _FakeDB()
    lines = run(db, legacy_ids=["rel-album"])
    assert lines[-1]["total_added"] == 2
    assert db.lookup_calls[0]["artist_id"] == "dz-42"
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [7, 7]


def test_an_other_artist_album_in_an_artist_payload_uses_its_own_name(run):
    """A featured/other-artist release inside one artist's payload takes
    the name-only lookup for ITS artist, not the envelope artist's id."""
    db = _FakeDB()
    db.profile_by_name = {"Yellowcard": 7, "Other Artist": 3}
    lines = run(db, albums=[
        {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
         "artist_name": "Yellowcard"},
        {"id": "rel-other", "name": "Other Album", "source": "deezer",
         "artist_name": "Other Artist"},
    ])
    assert lines[-1]["total_added"] == 3
    assert [kw["quality_profile_id"] for kw in db.wishlist_kwargs] == [
        7, 7, 3]
    other = [c for c in db.lookup_calls
             if c["artist_name"] == "Other Artist"]
    assert other and all(c["artist_id"] is None for c in other)


def test_a_batch_resolves_each_albums_own_artist(run):
    """The Discover "Your Albums" batch posts many artists to this one
    endpoint under a placeholder envelope artist: each album queues under
    ITS artist's profile, and the envelope lends nobody its (non-)profile.
    The batch artist is looked up by name only — there is no envelope id
    that could be theirs."""
    db = _FakeDB()
    db.profile_by_name = {"Yellowcard": 7, "Other Artist": 3}
    lines = run(
        db, artist_id="your-albums", envelope_name="Your Albums",
        albums=[
            {"id": "rel-album", "name": "Ocean Avenue", "source": "deezer",
             "artist_name": "Yellowcard"},
            {"id": "rel-other", "name": "Other Album", "source": "deezer",
             "artist_name": "Other Artist"},
        ])
    assert lines[-1]["total_added"] == 3
    by_call = db.wishlist_kwargs
    assert [kw["quality_profile_id"] for kw in by_call] == [7, 7, 3]
    assert all(c["artist_id"] is None for c in db.lookup_calls)
    assert {c["artist_name"] for c in db.lookup_calls} == {
        "Yellowcard", "Other Artist"}


# ── the lookup itself, against a real database ─────────────────────────────


def _real_db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / "m.db"))


def _watch(db, name, profile_id=1, qp=None, **ids):
    with db._get_connection() as conn:
        cols = ["artist_name", "profile_id", "quality_profile_id", *ids]
        conn.execute(
            f"INSERT INTO watchlist_artists ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            (name, profile_id, qp, *ids.values()))
        conn.commit()


def _extra_profile(db, name):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO quality_profiles (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid


def test_lookup_matches_any_provider_id_scoped_to_the_profile(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, deezer_artist_id="dz-42")
    _watch(db, "Someone Else", profile_id=2, qp=2, spotify_artist_id="dz-42")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard") == 2
    # another user's watchlist row is not this user's assignment
    assert lookup("dz-42", profile_id=3, artist_name="Yellowcard") is None


def test_lookup_falls_back_to_the_name_when_the_id_is_from_another_source(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("dz-42", profile_id=1, artist_name="yellowcard") == 2


def test_lookup_name_fallback_refuses_an_ambiguous_name(tmp_path):
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")
    _watch(db, "Yellowcard", qp=third, deezer_artist_id="dz-42")

    lookup = db.get_watchlist_artist_quality_profile_id
    # the id match still wins outright; only the name-only path is ambiguous
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard") == third
    assert lookup("it-7", profile_id=1, artist_name="Yellowcard") is None


def test_lookup_ignores_a_dangling_profile_id(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=9999, deezer_artist_id="dz-42")

    # add_to_wishlist rejects unknown profile ids outright, so handing one
    # through would queue nothing at all — a deleted profile reads as unset
    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=1, artist_name="Yellowcard") is None


def test_lookup_returns_none_without_an_assignment(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=None, deezer_artist_id="dz-42")
    _watch(db, "Unrelated", qp=2, spotify_artist_id="sp-1")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard") is None
    assert lookup("nope", profile_id=1, artist_name="Nobody") is None


def test_an_id_matched_row_with_no_profile_is_authoritative(tmp_path):
    """The artist IS watched and deliberately has no profile: a different
    same-named artist's assignment must not leak in through the name
    fallback."""
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=None, deezer_artist_id="dz-42")
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")

    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=1, artist_name="Yellowcard") is None


def test_duplicate_id_rows_are_disambiguated_by_name(tmp_path):
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    _watch(db, "Yellowcard", qp=2, deezer_artist_id="dz-42")
    _watch(db, "Yellowcard Again", qp=third, deezer_artist_id="dz-42")

    lookup = db.get_watchlist_artist_quality_profile_id
    # the request's name picks its own row out of the duplicates
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard") == 2
    assert lookup("dz-42", profile_id=1,
                  artist_name="Yellowcard Again") == third
    # a name neither row bears leaves the disagreement standing:
    # ambiguous, not a coin flip
    assert lookup("dz-42", profile_id=1, artist_name="Someone") is None


def test_lookup_translates_a_library_row_id(tmp_path):
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id) VALUES (?, ?)",
            ("Yellowcard", "sp-9")).lastrowid
        conn.commit()
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup(str(lib2_id), profile_id=1, artist_name="Yellowcard",
                  source="library") == 2
    assert lookup(f"lib2:{lib2_id}", profile_id=1,
                  artist_name="Yellowcard") == 2


def test_a_provider_id_is_never_read_as_a_library_row_id(tmp_path):
    """G02-4: the caller says the id is Deezer's, and a lib2 row happens to
    share the number — the Deezer artist's profile wins, the library
    artist's providers are never consulted."""
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id) VALUES (?, ?)",
            ("Library Artist", "sp-lib")).lastrowid
        conn.commit()
    _watch(db, "Library Artist", qp=2, spotify_artist_id="sp-lib")
    _watch(db, "Deezer Artist", qp=third, deezer_artist_id=str(lib2_id))

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup(str(lib2_id), profile_id=1, artist_name="Deezer Artist",
                  source="deezer") == third
    # and with no source, the direct provider match still wins outright
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Deezer Artist") == third


def test_name_fallback_covers_an_artist_filed_under_another_sources_id(tmp_path):
    """The fallback's whole purpose: the request's id is from a source
    the watchlist row doesn't carry (its provider label can't be trusted
    either — see the wrong-provider test), so only the name connects
    them. There is no provider exclusion: "row holds dz-1, request says
    dz-2" is exactly this case, not proof of a different artist."""
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, deezer_artist_id="dz-1")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("dz-2", profile_id=1, artist_name="Yellowcard",
                  source="deezer") == 2
    assert lookup("dz-1", profile_id=1, artist_name="Yellowcard",
                  source="deezer") == 2


def test_lookup_never_matches_a_context_sentinel_id(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Polluted", qp=2, spotify_artist_id="from_sync_modal")

    # #1284: a sentinel is not an id, however it got into the table
    assert db.get_watchlist_artist_quality_profile_id(
        "from_sync_modal", profile_id=1, artist_name="Someone Else") is None


def test_lookup_treats_a_missing_profile_id_as_the_default_profile(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, deezer_artist_id="dz-42")

    # get_current_profile_id() can hand back None; the rows live under 1
    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=None, artist_name="Yellowcard") == 2


def test_lookup_ignores_a_dangling_profile_reached_by_name(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=9999, spotify_artist_id="sp-9")

    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=1, artist_name="Yellowcard") is None


def test_lookup_survives_a_watchlist_table_without_the_column(tmp_path):
    import sqlite3
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("DROP COLUMN needs SQLite 3.35+")
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "ALTER TABLE watchlist_artists DROP COLUMN quality_profile_id")
        conn.commit()

    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=1, artist_name="Yellowcard") is None


def test_an_unknown_sources_id_is_never_read_as_a_library_row_id(tmp_path):
    """tidal/hydrabase have no watchlist column: their ids match nothing
    and are never reinterpreted — a numeric tidal id that happens to
    equal a lib2 row id must not inherit that artist's profile."""
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id) VALUES (?, ?)",
            ("Library Artist", "sp-lib")).lastrowid
        conn.commit()
    _watch(db, "Library Artist", qp=2, spotify_artist_id="sp-lib")

    lookup = db.get_watchlist_artist_quality_profile_id
    for source in ("tidal", "hydrabase", "youtube"):
        assert lookup(str(lib2_id), profile_id=1,
                      artist_name="Tidal Artist", source=source) is None


def test_an_id_labelled_with_the_wrong_provider_still_matches_its_row(tmp_path):
    """The discography modal picks the URL id Spotify > iTunes > Deezer
    from the enhanced record while `source` names the PAGE's provider —
    so a Spotify id routinely arrives labelled "deezer". Scoping the
    match to the labelled column (or excluding rows whose labelled
    column disagrees) loses the artist entirely; the name the request is
    about corroborates the row instead."""
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9",
           deezer_artist_id="dz-42")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("sp-9", profile_id=1, artist_name="Yellowcard",
                  source="deezer") == 2
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard",
                  source="spotify") == 2
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard",
                  source="deezer") == 2


def test_when_id_and_name_point_at_different_artists_the_name_wins(tmp_path):
    """The request queues tracks filtered by the NAMED artist, so when
    the id belongs to another watched artist entirely, the named
    artist's own assignment decides."""
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    _watch(db, "Row Artist", qp=2, spotify_artist_id="999")
    _watch(db, "Named Artist", qp=third, deezer_artist_id="dz-named")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup("999", profile_id=1, artist_name="Named Artist",
                  source="deezer") == third
    # and with the id's own name, the id's row decides as before
    assert lookup("999", profile_id=1, artist_name="Row Artist",
                  source="spotify") == 2


def test_library_translation_keeps_each_id_in_its_own_column(tmp_path):
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id, external_ids) "
            "VALUES (?, ?, ?)",
            ("Multi", "sp-multi", '{"deezer": "dz-multi"}')).lastrowid
        conn.commit()
    _watch(db, "Multi", qp=2, deezer_artist_id="dz-multi")
    # a decoy whose SPOTIFY id equals the lib2 artist's DEEZER id: pooled
    # candidates would match it and manufacture a disagreement
    _watch(db, "Decoy", qp=third, spotify_artist_id="dz-multi")

    assert db.get_watchlist_artist_quality_profile_id(
        str(lib2_id), profile_id=1, artist_name="Multi",
        source="library") == 2


def test_unset_and_dangling_duplicates_abstain(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=None, deezer_artist_id="dz-42")
    _watch(db, "Yellowcard", qp=9999, spotify_artist_id="sp-9")
    _watch(db, "Yellowcard", qp=2, discogs_artist_id="dg-1")

    lookup = db.get_watchlist_artist_quality_profile_id
    # by deezer: the only matched row is unset — authoritative default
    assert lookup("dz-42", profile_id=1, artist_name="Yellowcard") is None
    # by name the set is {unset, dangling, 2}: the first two abstain and
    # the one live assignment speaks
    assert lookup("zz-unknown", profile_id=1,
                  artist_name="Yellowcard") == 2


def test_name_fallback_folds_non_ascii_case(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "BJÖRK", qp=2, spotify_artist_id="sp-b")

    # SQLite LOWER() is ASCII-only; the fold happens in Python
    assert db.get_watchlist_artist_quality_profile_id(
        "zz-1", profile_id=1, artist_name="björk") == 2


def test_a_cross_namespace_collision_without_corroboration_decides_nothing(tmp_path):
    """A plain numeric id from one provider's namespace that happens to
    equal another artist's id in a different column, with neither the
    name nor the labelled column to corroborate it, is a collision —
    the unwatched artist must not inherit a stranger's profile."""
    db = _real_db(tmp_path)
    _watch(db, "Watched Itunes Artist", qp=2, itunes_artist_id="12345")

    assert db.get_watchlist_artist_quality_profile_id(
        "12345", profile_id=1, artist_name="Unwatched Deezer Artist",
        source="deezer") is None


def test_a_renamed_row_still_matches_in_its_labelled_column(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Renamed in Watchlist", qp=2, spotify_artist_id="sp-1")

    # the watchlist name is the user's own; the page sends the provider's
    assert db.get_watchlist_artist_quality_profile_id(
        "sp-1", profile_id=1, artist_name="Provider Name",
        source="spotify") == 2


def test_no_source_never_reinterprets_a_numeric_id_as_a_library_row(tmp_path):
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id) VALUES (?, ?)",
            ("Library Artist", "sp-lib")).lastrowid
        conn.commit()
    _watch(db, "Library Artist", qp=2, spotify_artist_id="sp-lib")

    lookup = db.get_watchlist_artist_quality_profile_id
    # the lib2 artist IS reachable by its name…
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Library Artist") == 2
    # …but an unrelated request carrying that bare number gets nothing:
    # without an explicit library label the number is never reread as a
    # library row id (G02-4)
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Other Artist") is None


def test_the_labelled_column_carve_out_and_its_accepted_risk(tmp_path):
    """Rule 3's carve-out, pinned deliberately: an artist who renamed
    THEMSELVES on the provider (same id, new name — watchlist rows are
    never renamed in the app, so the stored name is the provider's old
    one) still gets their profile when the id matches in the labelled
    provider's own column. The accepted other side of that coin: a
    foreign id mislabelled into this provider's namespace with the same
    number would match too. Corroboration can't tell the two apart from
    (id, name, source) alone; the coin flip is documented in the PR."""
    db = _real_db(tmp_path)
    _watch(db, "Old Provider Name", qp=2, deezer_artist_id="12345")

    assert db.get_watchlist_artist_quality_profile_id(
        "12345", profile_id=1, artist_name="New Provider Name",
        source="deezer") == 2


def test_the_labelled_column_carve_outs_collision_side(tmp_path):
    """The other side of the rule-3 coin, also pinned: the modal picks
    its id Spotify > iTunes > Deezer but labels it with the page's
    source, so an iTunes id can arrive labelled 'deezer'. If that number
    equals a watched stranger's Deezer id and the requested artist is
    not watched under their name, the stranger's profile is taken —
    indistinguishable from the provider-rename case above from
    (id, name, source) alone, and accepted for that reason."""
    db = _real_db(tmp_path)
    _watch(db, "Watched Stranger", qp=2, deezer_artist_id="12345")

    assert db.get_watchlist_artist_quality_profile_id(
        "12345", profile_id=1, artist_name="Unwatched Artist",
        source="deezer") == 2


def test_a_library_number_under_a_provider_label_is_corroborated(tmp_path):
    """The modal's fallback posts the library row number labelled with
    the page's source. Library numbers are small counting numbers — the
    likeliest collision of all — so the number is read as a library id
    when the row it numbers carries the request's name, and a watched
    stranger holding that number as a provider id is NOT picked up."""
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, spotify_id) "
            "VALUES (13, 'Library Artist', 'sp-lib')")
        conn.commit()
    _watch(db, "Library Artist", qp=2, spotify_artist_id="sp-lib")
    _watch(db, "Watched Stranger", qp=third, deezer_artist_id="13")

    assert db.get_watchlist_artist_quality_profile_id(
        "13", profile_id=1, artist_name="Library Artist",
        source="deezer") == 2


def test_a_corroborated_library_number_discards_its_provider_reading(
        tmp_path):
    """Accepted tradeoff, pinned: once the number is corroborated as a
    library row, its provider reading is discarded entirely. Here the
    library row's provider ids are stale (no watch row carries them),
    and the only row holding 13 as a Deezer id sits under an alias —
    indistinguishable from a stranger's row — so the answer is None,
    not that row's profile."""
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, spotify_id) "
            "VALUES (13, 'Library Artist', 'sp-stale')")
        conn.commit()
    _watch(db, "Filed Alias", qp=2, deezer_artist_id="13")

    assert db.get_watchlist_artist_quality_profile_id(
        "13", profile_id=1, artist_name="Library Artist",
        source="deezer") is None


def test_a_blank_lib2_name_is_no_evidence_and_never_vetoes(tmp_path):
    """A lib2 row with a blank name knows nothing about identity: it
    must not veto the translation its provider ids license."""
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, spotify_id) "
            "VALUES (41, '', 'sp-x')")
        conn.commit()
    _watch(db, "Filed Alias", qp=2, spotify_artist_id="sp-x")

    assert db.get_watchlist_artist_quality_profile_id(
        "41", profile_id=1, artist_name="Real Name",
        source="library") == 2


def test_a_fractional_stored_profile_abstains_but_an_integral_float_reads(
        tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2.5, deezer_artist_id="dz-frac")
    _watch(db, "Yellowcard", qp=2.0, spotify_artist_id="sp-int")

    # 2.5 is corrupt, not profile 2: the row abstains, lookup is None
    assert db.get_watchlist_artist_quality_profile_id(
        "dz-frac", profile_id=1, artist_name="Yellowcard") is None
    # a stored REAL that is integral is just profile 2
    assert db.get_watchlist_artist_quality_profile_id(
        "sp-int", profile_id=1, artist_name="Yellowcard") == 2


def test_a_library_label_whose_row_names_someone_else_is_vetoed(tmp_path):
    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, spotify_id) "
            "VALUES (21, 'Someone Else', 'sp-other')")
        conn.commit()
    _watch(db, "Someone Else", qp=2, spotify_artist_id="sp-other")
    _watch(db, "Requested", qp=third, spotify_artist_id="sp-req")

    # the label says library, but row 21 is not the requested artist:
    # no translation — the requested artist's own rows decide
    assert db.get_watchlist_artist_quality_profile_id(
        "21", profile_id=1, artist_name="Requested",
        source="library") == third
    # and the row's own artist, asked for by name, translates fine —
    # including under a lib2: prefix with a provider-flavoured label
    assert db.get_watchlist_artist_quality_profile_id(
        "lib2:21", profile_id=1, artist_name="Someone Else",
        source="deezer") == 2


def test_a_library_number_labelled_as_a_provider_resolves_via_its_ids(
        tmp_path):
    """The modal posts a library row number labelled with the page's
    provider source. The number alone is never reinterpreted (G02-4) —
    but the lib2 row it numbers carries the request's name, and THAT
    corroboration licenses the translation: the row's own provider ids
    then identify the watchlist row, even one filed under another name.
    Without the name corroboration (a different request name), the
    number stays a provider id and nothing is translated."""
    db = _real_db(tmp_path)
    with db._get_connection() as conn:
        lib2_id = conn.execute(
            "INSERT INTO lib2_artists (name, spotify_id) VALUES (?, ?)",
            ("Library Artist", "sp-lib")).lastrowid
        conn.commit()
    _watch(db, "Filed Under Another Name", qp=2, spotify_artist_id="sp-lib")

    lookup = db.get_watchlist_artist_quality_profile_id
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Library Artist", source="deezer") == 2
    # the same row is also reachable under its filed name
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Filed Under Another Name",
                  source="deezer") == 2
    # but a request naming somebody else gets no translation at all
    assert lookup(str(lib2_id), profile_id=1,
                  artist_name="Unrelated Artist", source="deezer") is None


def _no_duplicate_tracks(monkeypatch):
    from core.settings import config_manager
    real_get = config_manager.get

    def fake_get(key, default=None):
        if key == "wishlist.allow_duplicate_tracks":
            return False
        return real_get(key, default)

    monkeypatch.setattr(config_manager, "get", fake_get)


def _queued_row(db, track_id="t-1"):
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT quality_profile_id, failure_reason, source_type "
            "FROM wishlist_tracks WHERE spotify_track_id LIKE ?",
            (f"{track_id}%",)).fetchone()


def test_with_duplicates_off_an_opted_out_caller_skips_existing_rows(
        tmp_path, monkeypatch):
    """The allow_duplicates=False branch: carrying an explicit profile
    used to trigger a full refresh of a same-name row. With
    overwrite_existing_profile=False the row is skipped untouched —
    discography's exact pre-#2022 behaviour."""
    db = _real_db(tmp_path)
    _no_duplicate_tracks(monkeypatch)
    track = {"id": "t-1", "name": "Ocean Avenue",
             "artists": [{"name": "Yellowcard"}],
             "album": {"id": "a-1", "name": "Ocean Avenue"}}
    assert db.add_to_wishlist(
        track_data=track, failure_reason="from a playlist",
        source_type="playlist") is True
    before = _queued_row(db)

    assert db.add_to_wishlist(
        track_data=track, failure_reason="Added via Download Discography",
        source_type="discography", quality_profile_id=2,
        overwrite_existing_profile=False) is False
    assert _queued_row(db) == before


def test_an_enhance_refresh_still_honours_the_opt_out(tmp_path, monkeypatch):
    """source_type='enhance' enters the duplicates-off refresh branch
    on its own; the flag must still protect the stored profile there
    (the rest of the refresh is the enhance contract and does run)."""
    db = _real_db(tmp_path)
    _no_duplicate_tracks(monkeypatch)
    track = {"id": "t-1", "name": "Ocean Avenue",
             "artists": [{"name": "Yellowcard"}],
             "album": {"id": "a-1", "name": "Ocean Avenue"}}
    assert db.add_to_wishlist(
        track_data=track, failure_reason="original",
        source_type="playlist") is True
    before = _queued_row(db)

    assert db.add_to_wishlist(
        track_data=track, failure_reason="enhanced",
        source_type="enhance", quality_profile_id=2,
        overwrite_existing_profile=False) is False
    row = _queued_row(db)
    assert row[0] == before[0]          # profile protected
    assert row[1] == "enhanced"         # the refresh itself still ran


def test_an_enhance_refresh_with_duplicates_on_honours_the_opt_out(
        tmp_path):
    """The duplicates-on (default) exact-id refresh site: same opt-out
    contract as the duplicates-off branch — the refresh runs, the
    stored profile does not move."""
    db = _real_db(tmp_path)
    track = {"id": "t-1", "name": "Ocean Avenue",
             "artists": [{"name": "Yellowcard"}],
             "album": {"id": "a-1", "name": "Ocean Avenue"}}
    assert db.add_to_wishlist(
        track_data=track, failure_reason="original",
        source_type="playlist") is True
    before = _queued_row(db)

    assert db.add_to_wishlist(
        track_data=track, failure_reason="enhanced",
        source_type="enhance", quality_profile_id=2,
        overwrite_existing_profile=False) is False
    row = _queued_row(db)
    assert row[0] == before[0]          # profile protected
    assert row[2] == "enhance"          # the refresh itself still ran


def test_with_duplicates_off_the_default_caller_still_refreshes(
        tmp_path, monkeypatch):
    """The scanner's contract is untouched: overwrite_existing_profile
    defaults to True, and an explicit profile still refreshes the row."""
    db = _real_db(tmp_path)
    _no_duplicate_tracks(monkeypatch)
    track = {"id": "t-1", "name": "Ocean Avenue",
             "artists": [{"name": "Yellowcard"}],
             "album": {"id": "a-1", "name": "Ocean Avenue"}}
    assert db.add_to_wishlist(
        track_data=track, failure_reason="from a playlist",
        source_type="playlist") is True

    assert db.add_to_wishlist(
        track_data=track, failure_reason="watchlist scan",
        source_type="watchlist", quality_profile_id=2) is False
    row = _queued_row(db)
    assert row[0] == 2
    assert row[1] == "watchlist scan"


def test_a_sourceless_numeric_id_match_is_trusted_as_the_only_claim(tmp_path):
    """Legacy/sourceless payloads (the playlist explorer): with no
    source label at all, an id match under another name is the only
    identity claim there is and is trusted — pinned alongside the
    labelled-collision pin, as the same documented coin."""
    db = _real_db(tmp_path)
    _watch(db, "Watched Stranger", qp=2, deezer_artist_id="12345")

    assert db.get_watchlist_artist_quality_profile_id(
        "12345", profile_id=1, artist_name="Unwatched Artist") == 2


def test_a_stringified_null_id_is_no_id(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")
    lookup = db.get_watchlist_artist_quality_profile_id

    # the explorer's nullable id, stringified: falls back to the name
    assert lookup("null", profile_id=1, artist_name="Yellowcard") == 2
    assert lookup("undefined", profile_id=1,
                  artist_name="Unwatched") is None


def test_the_library_alias_map_covers_every_watchlist_source():
    """The P1-05 drift guard: a provider added to SOURCE_COLUMNS
    without an ARTIST_IDS_SQL alias here would be silently skipped by
    the library translation. The map must also stay identical to the
    positional tuple library_artist_provider_ids reads."""
    import re

    from core.library2.provider_ids import ARTIST_IDS_SQL
    from core.watchlist_sources import (
        _LIBRARY_ARTIST_ID_COLUMNS,
        SOURCE_COLUMNS,
    )
    from database.music_database import _LIBRARY_ARTIST_ID_ALIASES

    assert set(SOURCE_COLUMNS) <= set(_LIBRARY_ARTIST_ID_ALIASES)
    assert _LIBRARY_ARTIST_ID_ALIASES == dict(
        zip(SOURCE_COLUMNS, _LIBRARY_ARTIST_ID_COLUMNS, strict=True))
    for alias in _LIBRARY_ARTIST_ID_ALIASES.values():
        # a real output column of the query: "x AS alias", or the bare
        # column itself (musicbrainz_id is selected unaliased)
        assert re.search(
            rf"(?:AS\s+{re.escape(alias)}\b|^\s*{re.escape(alias)}\s*,)",
            ARTIST_IDS_SQL, re.MULTILINE)


def test_the_batch_placeholder_matches_the_frontend_constant():
    """_BATCH_ARTIST_ID hand-copies the frontend's BATCH_ENDPOINT; pin
    the two together so a rename on either side fails loudly here."""
    from pathlib import Path

    from api import artist_detail as ad

    ts_path = (Path(__file__).resolve().parents[1] / "webui" / "src"
               / "routes" / "discover" / "-discover.your-albums-actions.ts")
    if not ts_path.exists():
        pytest.skip("frontend source tree not present in this test run")
    ts = ts_path.read_text()
    import re
    assert re.search(
        r"BATCH_ENDPOINT\s*=\s*['\"][^'\"]*/api/artist/"
        + re.escape(ad._BATCH_ARTIST_ID) + r"/download-discography['\"]",
        ts)


def test_a_non_numeric_stored_profile_abstains_instead_of_breaking(tmp_path):
    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp="abc", deezer_artist_id="dz-42")
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")

    # the corrupt row must not take the whole lookup down with it
    assert db.get_watchlist_artist_quality_profile_id(
        "dz-42", profile_id=1, artist_name="Yellowcard") is None
    assert db.get_watchlist_artist_quality_profile_id(
        "zz-1", profile_id=1, artist_name="Yellowcard") == 2


def test_endpoint_with_a_real_db_and_a_mislabelled_id(tmp_path, monkeypatch):
    """End to end, no fakes on the DB side: the artist page sends the
    Spotify id it picked from the enhanced record, labelled with the
    page's Deezer source, for an album whose own (gap-fill) source is
    Spotify again. The wishlist rows must still land on the watchlist
    artist's profile — this is the exact #2022 flow."""
    from api import artist_detail as ad
    import database.music_database as mdb
    from core.metadata import album_tracks

    db = _real_db(tmp_path)
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9",
           deezer_artist_id="dz-42")
    monkeypatch.setattr(mdb, "MusicDatabase", lambda: db)
    monkeypatch.setattr(ad, "get_current_profile_id", lambda: 1,
                        raising=False)

    def fake_tracks(album_id, artist_name="", album_name="",
                    source_override=None):
        album, names, artist = RELEASES[album_id]
        return {"success": True, "source": "deezer",
                "album": dict(album, id=album_id,
                              artists=[{"name": artist}]),
                "tracks": [{"id": f"{album_id}-{i}", "name": n,
                            "artists": [{"name": artist}],
                            "track_number": i + 1}
                           for i, n in enumerate(names)]}

    monkeypatch.setattr(album_tracks, "get_artist_album_tracks", fake_tracks)
    monkeypatch.setattr(df, "load_global_content_filter_settings",
                        lambda _cm: {}, raising=False)

    app = Flask(__name__)
    app.register_blueprint(ad.bp)
    client = app.test_client()
    resp = client.post("/api/artist/sp-9/download-discography", json={
        "artist_name": "Yellowcard",
        "source": "deezer",
        "albums": [{"id": "rel-album", "name": "Ocean Avenue",
                    "source": "spotify", "artist_name": "Yellowcard"}],
    })
    lines = [json.loads(line)
             for line in resp.get_data(as_text=True).splitlines()
             if line.strip()]
    assert lines[-1]["total_added"] == 2
    with db._get_connection() as conn:
        stored = [r[0] for r in conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks").fetchall()]
    assert stored == [2, 2]


def test_requeueing_after_a_profile_change_leaves_existing_rows_alone(
        tmp_path, monkeypatch):
    """Discography passes overwrite_existing_profile=False: the profile
    applies to rows the click CREATES, and a row already queued — from
    an earlier click, a playlist, anywhere — keeps the profile it has.
    (The watchlist scanner keeps the authoritative-refresh contract;
    this endpoint never had it, and #2022 doesn't ask for it.)"""
    from api import artist_detail as ad
    import database.music_database as mdb
    from core.metadata import album_tracks

    db = _real_db(tmp_path)
    third = _extra_profile(db, "Third")
    _watch(db, "Yellowcard", qp=2, spotify_artist_id="sp-9")
    monkeypatch.setattr(mdb, "MusicDatabase", lambda: db)
    monkeypatch.setattr(ad, "get_current_profile_id", lambda: 1,
                        raising=False)

    def fake_tracks(album_id, artist_name="", album_name="",
                    source_override=None):
        album, names, artist = RELEASES[album_id]
        return {"success": True, "source": "deezer",
                "album": dict(album, id=album_id,
                              artists=[{"name": artist}]),
                "tracks": [{"id": f"{album_id}-{i}", "name": n,
                            "artists": [{"name": artist}],
                            "track_number": i + 1}
                           for i, n in enumerate(names)]}

    monkeypatch.setattr(album_tracks, "get_artist_album_tracks", fake_tracks)
    monkeypatch.setattr(df, "load_global_content_filter_settings",
                        lambda _cm: {}, raising=False)

    app = Flask(__name__)
    app.register_blueprint(ad.bp)
    client = app.test_client()
    body = {"artist_name": "Yellowcard", "source": "deezer",
            "albums": [{"id": "rel-album", "name": "Ocean Avenue",
                        "source": "deezer", "artist_name": "Yellowcard"}]}
    # the responses are streamed NDJSON — read them fully, or the
    # generator is abandoned mid-stream and later albums never queue
    client.post("/api/artist/sp-9/download-discography",
                json=body).get_data(as_text=True)
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE watchlist_artists SET quality_profile_id = ?", (third,))
        conn.commit()
    # second click: the same album again (already queued) PLUS a new one
    body["albums"] = body["albums"] + [
        {"id": "rel-lights", "name": "Lights and Sounds",
         "source": "deezer", "artist_name": "Yellowcard"}]
    client.post("/api/artist/sp-9/download-discography",
                json=body).get_data(as_text=True)
    with db._get_connection() as conn:
        stored = [r[0] for r in conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks "
            "ORDER BY id").fetchall()]
    # the already-queued tracks kept the profile they were queued with;
    # only the newly created row carries the artist's current profile
    assert stored == [2, 2, third]
