"""Guest credits settle from the library's own knowledge, no network.

The live upgrade of a 310k-track library made 28,860 artists out of track
credits like "1788-L, Deathpact" (credit strings, not artists), and the healer
then looked each up online, one by one: 39 done in 35 minutes, weeks to go.
18,512 of them carried the album's own artist as one part, 44 more had every
part already known. Those split here, at import and in the healer; a real
band name ("Earth, Wind & Fire") never does.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.importer import _credit_names_for_import, normalize_name
from core.matching.artist_credit import decide_credit
from core.library2.native_enrich import split_combined_credits_locally
from core.library2.schema import ensure_library_v2_schema


# ── the importer ─────────────────────────────────────────────────────────────

def _known(*names):
    keys = {normalize_name(n) for n in names}
    return lambda name: normalize_name(name) in keys


def _proposes(credit, *, album_artist=None, library=()):
    """what Split Artists proposes for an imported credit, from the same
    context the import has (the album's artist, the artists the library has)"""
    known = _known(*library)
    return list(decide_credit(credit, known=[album_artist] if album_artist else [],
                              is_act=known, is_artist=known).names)


def test_the_import_keeps_a_credit_as_written():
    # who an artist is, is Split Artists' to say; the import records
    assert _credit_names_for_import("1788-L, Deathpact") == ["1788-L, Deathpact"]
    assert _credit_names_for_import("1788-L feat. Deathpact") == ["1788-L", "Deathpact"]
    assert _credit_names_for_import("  ") == []


def test_a_credit_with_the_album_artist_in_it_is_a_list():
    assert _proposes("1788-L, Deathpact", album_artist="1788-L",
                     library=("1788-L",)) == ["1788-L", "Deathpact"]


def test_without_the_album_artist_an_unknown_band_name_stays_whole():
    # one known part on someone else's album is not enough
    assert _proposes("Earth, Wind & Fire", album_artist="Various Artists",
                     library=("Earth",)) == ["Earth, Wind & Fire"]


def test_a_band_on_its_own_album_is_never_split():
    assert _proposes("Earth, Wind & Fire", album_artist="Earth, Wind & Fire",
                     library=("Earth, Wind & Fire",)) == ["Earth, Wind & Fire"]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    return c


def _artist(conn, name, spotify=None, legacy=None):
    return conn.execute("INSERT INTO lib2_artists(name, name_key, spotify_id, legacy_artist_id) "
                        "VALUES(?,?,?,?)", (name, normalize_name(name), spotify, legacy)).lastrowid


def _credited_track(conn, album_artist, credited, title="Song"):
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
                         (album_artist,)).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, ?)",
                         (album, title)).lastrowid
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                 "VALUES(?,?, 'primary', 0)", (track, album_artist))
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                 "VALUES(?,?, 'featured', 1)", (track, credited))
    return track


def _provider(conn, track, *names):
    """the list a provider credits on the track: what confirms a split"""
    for position, name in enumerate(names):
        conn.execute("INSERT INTO lib2_provider_credits(entity_type, entity_id, source, "
                     "position, name, source_artist_id) VALUES('track', ?, 'deezer', ?, ?, ?)",
                     (track, position, name, f"dz-{name}"))


def _credits(conn, track):
    return [r[0] for r in conn.execute(
        "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
        "WHERE ta.track_id=? ORDER BY ta.position, a.name", (track,))]


def _exists(conn, artist_id):
    return conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (artist_id,)).fetchone() is not None


@pytest.fixture
def asked(monkeypatch):
    """what the pass hands to Split Artists, the one job that splits"""
    import core.repair_worker as rw
    calls = []
    monkeypatch.setattr(rw, "request_artist_split",
                        lambda ids, reason="": calls.extend(ids) or True)
    return calls


def test_a_ghost_with_the_album_artist_in_it_goes_to_split_artists(conn, asked):
    lead = _artist(conn, "1788-L", spotify="sp-1788", legacy=5)
    ghost = _artist(conn, "1788-L, Deathpact")
    track = _credited_track(conn, lead, ghost)

    stats = split_combined_credits_locally(conn)

    assert stats["handed_to_split_artists"] == 1 and asked == [ghost]
    # nothing split here: the credit stands until Split Artists confirms it
    assert _exists(conn, ghost) and _credits(conn, track) == ["1788-L", "1788-L, Deathpact"]


def test_a_ghost_named_like_a_known_artist_merges(conn, asked):
    real = _artist(conn, "Tyler, The Creator", spotify="sp-tyler", legacy=9)
    host = _artist(conn, "Kali Uchis", spotify="sp-kali", legacy=10)
    ghost = _artist(conn, "Tyler, The Creator")
    track = _credited_track(conn, host, ghost)

    stats = split_combined_credits_locally(conn)

    # one artist written the same way twice: a merge, not a split. The real
    # "Tyler, The Creator" reads as a list too: Split Artists keeps it whole
    assert stats["merged"] == 1 and asked == [real]
    assert not _exists(conn, ghost)
    assert _credits(conn, track) == ["Kali Uchis", "Tyler, The Creator"]
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE artist_id=?",
                        (real,)).fetchone()[0] == 1


@pytest.mark.parametrize("name, spotify", [
    ("Crosby, Stills & Nash", None),              # an unknown band
    ("Asian Doll & Antha Pantha", None),          # every part known
    ("Øneheart, Malek", "sp-wrong-match"),        # an id picked up by name
    ("Earth, Wind & Fire", "sp-ewf"),             # an identified band
])
def test_every_name_reading_as_a_list_goes_to_split_artists_untouched(conn, asked, name, spotify):
    host = _artist(conn, "Various Artists", spotify="sp-va", legacy=11)
    for part in ("Asian Doll", "Antha Pantha", "Øneheart"):
        _artist(conn, part, spotify=f"sp-{part}")
    ghost = _artist(conn, name, spotify=spotify)
    track = _credited_track(conn, host, ghost)

    split_combined_credits_locally(conn)

    assert asked == [ghost]
    assert _exists(conn, ghost) and _credits(conn, track) == ["Various Artists", name]


def test_a_ghost_that_owns_an_album_is_never_touched(conn, asked):
    ghost = _artist(conn, "Owner, Of Albums")
    conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Mine')", (ghost,))
    stats = split_combined_credits_locally(conn)
    assert stats["considered"] == 0 and _exists(conn, ghost) and asked == []


def test_the_automatic_online_pass_only_takes_what_the_workers_cannot(conn):
    """A plain single name is every enrichment worker's to match; the online
    pass after the upgrade only walks the combined credits they skip."""
    from core.library2.native_enrich import _pending_unmapped_artists
    single = _artist(conn, "Geino")
    combined = _artist(conn, "Skratch Bastid, Shad")
    everything = {r["id"] for r in _pending_unmapped_artists(conn, None)}
    automatic = {r["id"] for r in _pending_unmapped_artists(conn, None, combined_only=True)}
    assert {single, combined} <= everything
    assert automatic == {combined}


# ── the band a frontman leads ────────────────────────────────────────────────
# "<artist> & The <band>" carries the album artist as a part and is still one
# artist. the anchor rule read it as a list: "Nick Cave & The Bad Seeds" on a
# Nick Cave album became Nick Cave plus a new, unidentified "The Bad Seeds".

@pytest.mark.parametrize("band, lead", [
    ("Nick Cave & The Bad Seeds", "Nick Cave"),
    ("Tom Petty and the Heartbreakers", "Tom Petty"),
    ("Marina and the Diamonds", "Marina"),
    ("Florence + the Machine", "Florence"),
])
def test_the_band_a_frontman_leads_stays_whole(band, lead):
    assert _proposes(band, album_artist=lead, library=(lead,)) == [band]


def test_a_list_with_the_album_artist_is_proposed_as_a_list():
    assert _proposes("Drake & Future", album_artist="Drake",
                     library=("Drake",)) == ["Drake", "Future"]
    # the feat. makes Lauryn Hill a guest; it does not make the band a list
    assert _proposes("Bob Marley & The Wailers feat. Lauryn Hill", album_artist="Bob Marley",
                     library=("Bob Marley",)) == ["Bob Marley & The Wailers", "Lauryn Hill"]


def test_an_identified_band_a_frontman_leads_keeps_its_watch(conn, asked):
    lead = _artist(conn, "Bob Marley", spotify="sp-marley")
    band = _artist(conn, "Bob Marley & The Wailers", spotify="sp-wailers")
    conn.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id, monitored, "
                 "provenance) VALUES('artist', ?, 3, 1, 'user_explicit')", (band,))
    track = _credited_track(conn, lead, band)

    split_combined_credits_locally(conn)

    assert _exists(conn, band)
    assert _credits(conn, track) == ["Bob Marley", "Bob Marley & The Wailers"]
    assert conn.execute("SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_id=?",
                        (band,)).fetchone()[0] == 1


def test_a_ghost_split_artists_already_decided_is_not_asked_about_again(conn, asked):
    from core.library2.schema import ensure_library_v2_schema  # noqa: F401
    host = _artist(conn, "Various Artists", spotify="sp-va", legacy=11)
    ghost = _artist(conn, "Crosby, Stills & Nash")
    _credited_track(conn, host, ghost)
    conn.execute("CREATE TABLE IF NOT EXISTS repair_findings(id INTEGER PRIMARY KEY, job_id TEXT, "
                 "entity_type TEXT, entity_id TEXT, status TEXT)")
    conn.execute("INSERT INTO repair_findings(job_id, entity_type, entity_id, status) "
                 "VALUES('comma_artist_splitter', 'artist', ?, 'pending')", (f"lib2:{ghost}",))
    split_combined_credits_locally(conn)
    assert asked == []
