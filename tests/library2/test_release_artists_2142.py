"""#2142 second pass: the Artists list is the artists whose releases you have.

Ericsson added 12th Planet and 16BL and got PhaseOne, Rampa and every
performer of a 61-track compilation as cards of their own. A download that
didn't start from a Library v2 row filed its release under the track's lead
artist while the file was tagged (and foldered) under the album artist, so
each performer held a release of their own; and the first fix's rule let
anyone leading most of a release's tracks in, which a one-track row of a
17-track mix or a one-track single always is.
"""

from __future__ import annotations

import json

import pytest

from core.library2 import autolink as A
from core.library2 import queries as Q


@pytest.fixture(autouse=True)
def _no_artwork_warmup(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_args, **_kwargs: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: (
        True if key == "features.library_v2" else real_get(key, default)))
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _download(path, title, track_artists, album, album_artists, *, total=1,
              album_type="single"):
    """an album download's context, the shape the album pipeline hands autolink"""
    return {
        "_final_processed_path": path,
        "username": "usenet",
        "spotify_artist": {"name": album_artists[0]},
        "spotify_album": {"name": album, "album_type": album_type, "total_tracks": total,
                          "artists": [{"name": n} for n in album_artists]},
        "track_info": {"name": title, "artists": [{"name": n} for n in track_artists],
                       "provider": "spotify"},
        "_embedded_id_tags": {},
    }


def _filed_under(conn, file_id):
    return conn.execute(
        "SELECT ar.name FROM lib2_track_files tf JOIN lib2_tracks t ON t.id = tf.track_id"
        " JOIN lib2_albums al ON al.id = t.album_id"
        " JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE tf.id = ?",
        (file_id,)).fetchone()[0]


def _listed(conn):
    return {a["name"] for a in Q.list_artists(conn, limit=500)[0]}


def _artist(conn, name, **cols):
    keys = ["name", *cols]
    return conn.execute(
        f"INSERT INTO lib2_artists({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
        (name, *cols.values())).lastrowid


def _album(conn, artist_id, title, **cols):
    keys = ["primary_artist_id", "title", "origin", *cols]
    album_id = conn.execute(
        f"INSERT INTO lib2_albums({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
        (artist_id, title, "library", *cols.values())).lastrowid
    conn.execute("INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
                 " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def _track(conn, album_id, title, credits, *, number=1, album_artist_tag=None, owned=True):
    """``credits``: [(artist_id, role)] in order"""
    track_id = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number)"
                            " VALUES(?,?,?)", (album_id, title, number)).lastrowid
    for position, (artist_id, role) in enumerate(credits):
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
                     " VALUES(?,?,?,?)", (track_id, artist_id, role, position))
    if owned:
        tags = json.dumps({"albumartist": album_artist_tag} if album_artist_tag else {})
        conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, tags_json)"
                     " VALUES(?,?,1,?)", (track_id, f"/music/x/{album_id}/{track_id}.flac", tags))
    return track_id


# --- filing a download --------------------------------------------------------

def test_a_dj_mix_track_files_under_the_mix_not_its_performer(lib2_enabled, imported_conn):
    """Rampa's shape: track 8 of 16BL's mix, by Rampa and WhoMadeWho"""
    file_id = A.link_download_into_library_v2(_download(
        "/music/16BL/Anjunadeep Open Air Prague/08 Tell Me Are We.m4a", "Tell Me Are We",
        ["Rampa", "WhoMadeWho"], "Anjunadeep Open Air Prague", ["16BL"],
        total=17, album_type="compilation"))

    assert _filed_under(imported_conn, file_id) == "16BL"
    listed = _listed(imported_conn)
    assert "16BL" in listed and "Rampa" not in listed
    # Rampa keeps the track credit: their page has the mix under Appears On
    rampa = imported_conn.execute("SELECT id FROM lib2_artists WHERE name='Rampa'").fetchone()[0]
    page = Q.get_artist(imported_conn, rampa)
    assert page["albums"] == [] and page["singles"] == []
    assert [a["title"] for a in page["appears_on"]] == ["Anjunadeep Open Air Prague"]


def test_a_single_files_under_its_album_artist_tag(lib2_enabled, imported_conn, monkeypatch):
    """PhaseOne's shape: the provider lists PhaseOne first on the single, the
    file's album artist tag says 12th Planet. the tag wins"""
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "12th Planet")
    file_id = A.link_download_into_library_v2(_download(
        "/music/12th Planet/Taco Bout It/01 Taco Bout It.flac", "Taco Bout It",
        ["PhaseOne", "12th Planet"], "Taco Bout It", ["PhaseOne", "12th Planet"]))

    assert _filed_under(imported_conn, file_id) == "12th Planet"
    listed = _listed(imported_conn)
    assert "12th Planet" in listed and "PhaseOne" not in listed
    # still findable: search lists credited artists, and so does "featured"
    assert "PhaseOne" in {a["name"] for a in Q.list_artists(imported_conn, credit="featured")[0]}


def test_a_collab_album_artist_stays_with_its_first_listed_artist(lib2_enabled, imported_conn,
                                                                monkeypatch):
    """"A & B" with the lead listed first is the lead's, as its folder is"""
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "Duo Lead & Partner")
    file_id = A.link_download_into_library_v2(_download(
        "/music/Duo Lead/Joint/01 Song.flac", "Song", ["Duo Lead", "Partner"], "Joint",
        ["Duo Lead", "Partner"], total=10, album_type="album"))
    assert _filed_under(imported_conn, file_id) == "Duo Lead"


# --- the rule -----------------------------------------------------------------

def test_leading_the_only_track_of_someone_elses_single_is_a_guest(imported_conn):
    """the first fix let a lead on more than half the tracks in: one track of
    12th Planet's one-track single is all of it"""
    planet = _artist(imported_conn, "12th Planet")
    phase = _artist(imported_conn, "PhaseOne")
    single = _album(imported_conn, planet, "Taco Bout It", album_type="single")
    _track(imported_conn, single, "Taco Bout It", [(phase, "primary"), (planet, "featured")])
    imported_conn.commit()

    listed = _listed(imported_conn)
    assert "12th Planet" in listed and "PhaseOne" not in listed
    page = Q.get_artist(imported_conn, phase)
    assert page["singles"] == [] and [a["title"] for a in page["appears_on"]] == ["Taco Bout It"]


def test_one_held_track_of_a_long_mix_is_not_most_of_it(imported_conn):
    """even with a stray primary album credit: 1 of 17 by the provider's count"""
    host = _artist(imported_conn, "16BL")
    rampa = _artist(imported_conn, "Rampa")
    mix = _album(imported_conn, host, "Anjunadeep Open Air Prague", expected_track_count=17)
    imported_conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role)"
                          " VALUES(?,?,'primary')", (mix, rampa))
    _track(imported_conn, mix, "Tell Me Are We", [(rampa, "primary")], number=8)
    imported_conn.commit()

    assert "Rampa" not in _listed(imported_conn)


def test_a_real_collaboration_is_both_artists(imported_conn):
    """an album credited to both: filed under one, the other credited primary
    on the album and on most of its tracks, or primary beside the album's
    artist on them"""
    first = _artist(imported_conn, "First Half")
    second = _artist(imported_conn, "Second Half")
    third = _artist(imported_conn, "Third Wheel")
    joint = _album(imported_conn, first, "Together")
    imported_conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role)"
                          " VALUES(?,?,'primary')", (joint, second))
    for n in range(3):
        _track(imported_conn, joint, f"Duet {n}",
               [(first, "primary"), (second, "primary"), (third, "primary")], number=n + 1)
    imported_conn.commit()

    listed = _listed(imported_conn)
    # second: an album credit and most tracks; third: primary beside first
    assert {"First Half", "Second Half", "Third Wheel"} <= listed
    assert [a["title"] for a in Q.get_artist(imported_conn, second)["albums"]] == ["Together"]


def test_a_watched_artist_with_nothing_filed_is_listed(imported_conn):
    _artist(imported_conn, "Wanted Act", monitored=1)
    imported_conn.commit()
    assert "Wanted Act" in _listed(imported_conn)


def test_a_bands_own_releases_and_a_guest_led_single_stay_theirs(imported_conn):
    """the band's releases are filed under them; their single that credits a
    guest first is still theirs, and the guest isn't a release artist"""
    band = _artist(imported_conn, "Home Band")
    guest = _artist(imported_conn, "Front Guest")
    lp = _album(imported_conn, band, "Home LP")
    for n in range(3):
        _track(imported_conn, lp, f"Song {n}", [(band, "primary")], number=n + 1,
               album_artist_tag="Home Band")
    single = _album(imported_conn, band, "Guest First", album_type="single")
    _track(imported_conn, single, "Guest First", [(guest, "primary"), (band, "featured")],
           album_artist_tag="Home Band")
    imported_conn.commit()

    listed = _listed(imported_conn)
    assert "Home Band" in listed and "Front Guest" not in listed
    page = Q.get_artist(imported_conn, band)
    assert {a["title"] for a in page["albums"] + page["singles"]} == {"Home LP", "Guest First"}

    # and the repair leaves them alone: the files name the band
    from core.library2.release_artist_repair import rehome_track_lead_releases
    stats = rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")
    assert stats["albums_moved"] == stats["albums_merged"] == 0


def test_a_stray_primary_credit_is_not_counted_on_the_artists_card(imported_conn):
    """the card's completeness reads the list's rule: a guest's stray primary
    album credit on someone else's release is no release of theirs"""
    from core.library2.artist_completeness import compute
    host = _artist(imported_conn, "Host Act")
    guest = _artist(imported_conn, "Verse Guest")
    own = _album(imported_conn, guest, "Own Record", expected_track_count=1)
    _track(imported_conn, own, "Own Song", [(guest, "primary")])
    hosted = _album(imported_conn, host, "Hosted Record")  # size never looked up
    imported_conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role)"
                          " VALUES(?,?,'primary')", (hosted, guest))
    _track(imported_conn, hosted, "Intro", [(host, "primary")], number=1)
    _track(imported_conn, hosted, "Verse", [(guest, "primary")], number=2)
    imported_conn.commit()

    fact = compute(imported_conn).artists[guest]
    assert fact.unverified_albums == 0 and fact.state == "complete"


# --- the repair ---------------------------------------------------------------

def test_the_repair_moves_a_mix_row_filed_under_its_performer(imported_conn):
    """Rampa's library as 3.6.7 left it: a one-track "Anjunadeep Open Air
    Prague" of Rampa's, tagged 16BL, with 16BL's own row of it beside"""
    from core.library2.release_artist_repair import rehome_track_lead_releases
    host = _artist(imported_conn, "16BL")
    rampa = _artist(imported_conn, "Rampa")
    who = _artist(imported_conn, "WhoMadeWho")
    mine = _album(imported_conn, host, "Anjunadeep Open Air Prague")
    _track(imported_conn, mine, "Opening", [(host, "primary")], number=1, album_artist_tag="16BL")
    split = _album(imported_conn, rampa, "Anjunadeep Open Air Prague")
    _track(imported_conn, split, "Tell Me Are We", [(rampa, "primary"), (who, "featured")],
           number=8, album_artist_tag="16BL")
    imported_conn.commit()
    assert "Rampa" in _listed(imported_conn)

    stats = rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")
    imported_conn.commit()

    assert stats["albums_merged"] == 1
    assert imported_conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                                 (split,)).fetchone()[0] == 0
    assert imported_conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?",
                                 (mine,)).fetchone()[0] == 2
    listed = _listed(imported_conn)
    assert "16BL" in listed and "Rampa" not in listed
    assert [a["title"] for a in Q.get_artist(imported_conn, rampa)["appears_on"]] == [
        "Anjunadeep Open Air Prague"]


def test_the_repair_folds_a_compilation_split_per_performer(imported_conn):
    """Paul McCartney 1/61, Neil Sedaka 2/61: one compilation, one row per
    performer, each with the provider's 61. its files say Various Artists"""
    from core.library2.artist_completeness import compute
    from core.library2.release_artist_repair import rehome_track_lead_releases
    paul = _artist(imported_conn, "Paul McCartney")
    neil = _artist(imported_conn, "Neil Sedaka")
    pieces = {}
    for artist, songs in ((paul, ["Band On The Run"]), (neil, ["Oh Carol", "Calendar Girl"])):
        album = _album(imported_conn, artist, "Sixties Gold", expected_track_count=61)
        pieces[artist] = album
        for n, song in enumerate(songs):
            _track(imported_conn, album, song, [(artist, "primary")], number=n + 1,
                   album_artist_tag="Various Artists")
    imported_conn.commit()
    assert {"Paul McCartney", "Neil Sedaka"} <= _listed(imported_conn)
    assert compute(imported_conn).artists[paul].total == 61

    stats = rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")
    imported_conn.commit()

    assert stats["albums_moved"] + stats["albums_merged"] == 2
    albums = imported_conn.execute(
        "SELECT al.id, ar.name FROM lib2_albums al JOIN lib2_artists ar"
        " ON ar.id = al.primary_artist_id WHERE al.title='Sixties Gold'").fetchall()
    assert [row[1] for row in albums] == ["Various Artists"]
    listed = _listed(imported_conn)
    assert "Paul McCartney" not in listed and "Neil Sedaka" not in listed
    facts = compute(imported_conn).artists
    assert facts[paul].total == 1 and facts[neil].total == 2  # their own tracks, not 61
    # each keeps their own songs on the track rows
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_track_artists ta JOIN lib2_tracks t ON t.id = ta.track_id"
        " WHERE t.album_id=? AND ta.artist_id IN (?,?)",
        (albums[0][0], paul, neil)).fetchone()[0] == 3


def test_the_repair_reads_the_file_when_no_snapshot_and_the_folder_disagrees(imported_conn):
    from core.library2.release_artist_repair import rehome_track_lead_releases
    planet = _artist(imported_conn, "12th Planet")
    phase = _artist(imported_conn, "PhaseOne")
    single = _album(imported_conn, phase, "Taco Bout It", album_type="single")
    track = _track(imported_conn, single, "Taco Bout It", [(phase, "primary")], owned=False)
    imported_conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary)"
                          " VALUES(?, '/music/12th Planet/Taco Bout It/01.flac', 1)", (track,))
    imported_conn.commit()
    reads = []

    stats = rehome_track_lead_releases(
        imported_conn, read_album_artist=lambda p: reads.append(p) or "12th Planet")
    imported_conn.commit()

    assert reads == ["/music/12th Planet/Taco Bout It/01.flac"]
    assert stats["albums_moved"] == 1
    assert imported_conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?",
                                 (single,)).fetchone()[0] == planet
    assert "PhaseOne" not in _listed(imported_conn)


def test_the_repair_leaves_a_locked_album_and_disagreeing_files(imported_conn):
    from core.library2.release_artist_repair import rehome_track_lead_releases
    lead = _artist(imported_conn, "Track Lead")
    mixed = _album(imported_conn, lead, "Mixed Up")
    _track(imported_conn, mixed, "One", [(lead, "primary")], number=1, album_artist_tag="Someone")
    _track(imported_conn, mixed, "Two", [(lead, "primary")], number=2, album_artist_tag="Else")
    hand_set = _album(imported_conn, lead, "Hand Set")
    _track(imported_conn, hand_set, "Three", [(lead, "primary")], album_artist_tag="Someone")
    from core.library2.locks import LIB2_ENTITY_LOCKS_DDL
    imported_conn.execute(LIB2_ENTITY_LOCKS_DDL)
    imported_conn.execute("INSERT INTO lib2_entity_locks(entity_type, entity_id, scopes)"
                          " VALUES('album', ?, 'tags')", (hand_set,))
    imported_conn.commit()

    stats = rehome_track_lead_releases(imported_conn, read_album_artist=lambda p: "")

    assert stats["albums_moved"] == stats["albums_merged"] == 0
