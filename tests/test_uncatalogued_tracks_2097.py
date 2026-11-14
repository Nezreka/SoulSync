"""#2097: a playlist can find the tracks no catalogue has.

a bootleg, a dj edit or an unofficial remix gets a wing it stub and used to
wait in the pool for a match that was never coming. with the playlist's
"find uncatalogued tracks" switch on its stubs are searched by the playlist's
own artist and title, a candidate has to be that exact version by its name,
and the file is tagged from the playlist.
"""

import json
from types import SimpleNamespace

import pytest
from flask import Flask, g

from core.discovery import wing_it
from core.playlists import uncatalogued as unc
from database.music_database import MusicDatabase

STUB = wing_it.stub_track_id("MIZE", "All I Wanted (MIZE Remix)")


@pytest.fixture(autouse=True)
def _fresh():
    unc.forget()
    yield
    unc.forget()


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db, name="Dubstep remixes", source_id="f1", stub=STUB):
    pk = db.mirror_playlist(
        source="file", source_playlist_id=source_id, name=name,
        tracks=[{"track_name": "All I Wanted (MIZE Remix)", "artist_name": "MIZE"}], profile_id=1)
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET extra_data=? WHERE playlist_id=?",
            (json.dumps({"discovered": True, "wing_it_fallback": True,
                         "matched_data": {"id": stub, "name": "All I Wanted (MIZE Remix)"}}), pk))
        conn.commit()
    return pk


# ── is this file the recording the playlist names ────────────────────────────

WANT = ("MIZE", "All I Wanted (MIZE Remix)")


@pytest.mark.parametrize("name", [
    "MIZE - All I Wanted (MIZE Remix).flac",
    r"@@abc\Dubstep\Paramore - All I Wanted [MIZE REMIX].mp3",
    "paramore_all_i_wanted_mize_remix_free_dl.mp3",
    "Paramore - All I Wanted (Mize Remix) [Free Download]",
])
def test_the_remix_by_its_name_is_taken(name):
    assert unc.strict_match(*WANT, name) == (True, "")


@pytest.mark.parametrize("name, why", [
    ("Paramore - All I Wanted.flac", "credit missing"),                      # the original
    ("Paramore - All I Wanted (Live at Reading).flac", "credit missing"),
    ("Paramore - All I Wanted (Skrillex Remix).mp3", "credit missing"),      # someone else's
    ("MIZE - Wanted (MIZE Remix).mp3", "title word missing"),
    ("MIZE - All I Wanted (MIZE Remix) [Slowed + Reverb].mp3", "a different version"),
    ("Someone - All I Wanted (MIZE Remix) instrumental.mp3", "a different version"),
])
def test_anything_else_is_not(name, why):
    ok, reason = unc.strict_match(*WANT, name)
    assert ok is False and reason.startswith(why), reason


def test_a_plain_title_never_takes_a_remix_of_it():
    assert unc.strict_match("DJ Set", "Warehouse Tool", "DJ Set - Warehouse Tool.wav")[0] is True
    ok, why = unc.strict_match("DJ Set", "Warehouse Tool", "DJ Set - Warehouse Tool (Other Remix).wav")
    assert ok is False and why.startswith("a different version")
    # a word that is part of the title is not a version
    assert unc.strict_match("Band", "Live Again", "Band - Live Again.mp3")[0] is True


def test_the_artist_has_to_be_in_the_name_any_one_of_them():
    ok, why = unc.strict_match("Nobody", "Rare Edit (VIP)", "Somebody - Rare Edit VIP.mp3")
    assert ok is False and why == "none of the artists is in the name"
    assert unc.strict_match("A & B feat. C", "Tune", "B - Tune.mp3")[0] is True
    assert unc.strict_match(["A", {"name": "Zed"}], "Tune", "Zed - Tune.mp3")[0] is True
    assert unc.strict_match("", "Tune", "whoever - Tune.mp3")[0] is True     # none given, none asked


def test_accents_punctuation_and_a_dash_credit_read_the_same():
    assert unc.strict_match("Beyoncé", "Déjà Vu - Freemasons Club Mix",
                            "beyonce deja vu (freemasons club mix).mp3")[0] is True
    assert unc.strict_match("Beyoncé", "Déjà Vu - Freemasons Club Mix",
                            "beyonce - deja vu.mp3")[0] is False
    assert unc.strict_match("Guns N' Roses", "Don't Cry (Bootleg)",
                            "Guns N Roses - Dont Cry bootleg.mp3")[0] is True
    assert unc.strict_match("X", "", "X.mp3") == (False, "the playlist gives no title")


def test_the_length_has_to_be_close_when_both_are_known():
    args = (*WANT, "MIZE - All I Wanted (MIZE Remix).flac")
    assert unc.strict_match(*args, wanted_ms=240000, candidate_ms=244000)[0] is True
    ok, why = unc.strict_match(*args, wanted_ms=240000, candidate_ms=200000)
    assert ok is False and why == "length is 40s off"
    assert unc.strict_match(*args, wanted_ms=0, candidate_ms=200000)[0] is True
    assert unc.strict_match(*args, wanted_ms=240000, candidate_ms=0)[0] is True
    # a long set gets a share of its length, not seven seconds
    assert unc.strict_match(*args, wanted_ms=3600000, candidate_ms=3690000)[0] is True


def test_a_candidates_length_is_read_in_seconds_or_milliseconds():
    assert unc.candidate_ms(SimpleNamespace(duration=241)) == 241000.0
    assert unc.candidate_ms(SimpleNamespace(duration=241000)) == 241000.0
    assert unc.candidate_ms(SimpleNamespace(duration=None)) == 0.0
    assert unc.candidate_ms(SimpleNamespace()) == 0.0


# ── which stubs are let through ──────────────────────────────────────────────

def test_only_a_playlist_with_the_switch_on_lets_its_stubs_through(mdb):
    pk = _mirror(mdb)
    assert unc.allowed_stub_ids(mdb) == frozenset()
    assert mdb.get_mirrored_playlist(pk)["allow_uncatalogued"] is False
    assert mdb.update_mirrored_playlist_preferences(pk, profile_id=1, allow_uncatalogued=True) == "ok"
    assert unc.allowed_stub_ids(mdb) == frozenset()            # still the half minute's answer
    unc.forget()
    assert unc.allowed_stub_ids(mdb) == frozenset({STUB})
    assert unc.is_allowed(STUB, mdb) is True
    assert unc.is_allowed("wing_it_000000000000", mdb) is False
    assert unc.is_allowed("spotify123", mdb) is False and unc.is_allowed(None, mdb) is False
    assert unc.track_is_uncatalogued({"id": STUB}, mdb) is True
    assert unc.track_is_uncatalogued(SimpleNamespace(id=STUB), mdb) is True
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, allow_uncatalogued=False)
    unc.forget()
    assert unc.is_allowed(STUB, mdb) is False                  # off stops it at once


def test_a_matched_track_in_that_playlist_is_not_a_stub(mdb):
    pk = _mirror(mdb, stub="deezer:99")
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, allow_uncatalogued=True)
    assert unc.allowed_stub_ids(mdb) == frozenset()


def test_an_unreadable_database_allows_nothing():
    class Broken:
        def _get_connection(self):
            raise RuntimeError("locked")
    assert unc.allowed_stub_ids(Broken()) == frozenset()


def test_the_wishlist_gate_lets_that_playlists_stub_on_and_no_other(mdb, monkeypatch):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, allow_uncatalogued=True)
    monkeypatch.setattr(wing_it, "wishlist_guesses_enabled", lambda: False)
    monkeypatch.setattr(unc, "allowed_stub_ids", lambda db=None: frozenset({STUB}))
    assert wing_it.should_wishlist_stub("MIZE", "All I Wanted (MIZE Remix)", STUB) is True
    assert wing_it.should_wishlist_stub("MIZE", "All I Wanted (MIZE Remix)") is False
    assert wing_it.should_wishlist_stub("MIZE", "All I Wanted (MIZE Remix)", "wing_it_other0000") is False
    assert wing_it.should_wishlist_stub("Unknown Artist", "x", STUB) is False   # nothing to search with
    monkeypatch.setattr(wing_it, "wishlist_guesses_enabled", lambda: True)
    assert wing_it.should_wishlist_stub("MIZE", "Song") is True                  # the app-wide setting still works


# ── the download only takes the named recording ──────────────────────────────

def _cand(filename, duration=0, **kw):
    return SimpleNamespace(filename=filename, duration=duration, username="peer", confidence=0.9, **kw)


def test_candidates_for_an_uncatalogued_track_are_held_to_its_name(monkeypatch):
    from core.downloads import validation
    track = SimpleNamespace(id=STUB, name="All I Wanted (MIZE Remix)", artists=["MIZE"], duration_ms=240000)
    original = _cand("Paramore - All I Wanted.flac", 225)
    remix = _cand("Paramore - All I Wanted (MIZE Remix).mp3", 241)
    wrong_length = _cand("MIZE - All I Wanted (MIZE Remix) preview.mp3", 60)
    monkeypatch.setattr(unc, "allowed_stub_ids", lambda db=None: frozenset({STUB}))
    why = {}
    kept = validation._keep_the_named_recording([original, remix, wrong_length], track, why)
    assert kept == [remix]
    assert {id(original), id(wrong_length)} == set(why)
    assert all(d.code == "version_conflict" for _row, d in why.values())


def test_every_other_track_keeps_the_usual_scoring(monkeypatch):
    from core.downloads import validation
    rows = [_cand("Paramore - All I Wanted.flac")]
    monkeypatch.setattr(unc, "allowed_stub_ids", lambda db=None: frozenset({STUB}))
    catalogued = SimpleNamespace(id="deezer:1", name="All I Wanted (MIZE Remix)", artists=["MIZE"], duration_ms=0)
    assert validation._keep_the_named_recording(rows, catalogued, None) == rows
    # a stub of a playlist without the switch: the wing it rules as they were
    other = SimpleNamespace(id="wing_it_ffffffffffff", name="x", artists=["y"], duration_ms=0)
    assert validation._keep_the_named_recording(rows, other, None) == rows
    assert validation._keep_the_named_recording([], catalogued, None) == []


def test_a_stream_is_read_by_its_artist_and_title():
    track = SimpleNamespace(id=STUB, name="All I Wanted (MIZE Remix)", artists=["MIZE"], duration_ms=240000)
    stream = SimpleNamespace(artist="MIZE", title="All I Wanted (MIZE Remix)", filename="123||x", duration=240000)
    original = SimpleNamespace(artist="Paramore", title="All I Wanted", filename="9||y", duration=225000)
    assert unc.filter_candidates([stream, original], track) == [stream]


# ── the file is tagged from the playlist ─────────────────────────────────────

def test_an_uncatalogued_download_is_hand_tagged_and_nothing_else_changes(monkeypatch):
    from core.metadata.manual import is_manual_context
    monkeypatch.setattr(unc, "allowed_stub_ids", lambda db=None: frozenset({STUB}))
    assert is_manual_context({"track_info": {"id": STUB}}) is True
    assert is_manual_context({"track_info": {"id": "wing_it_ffffffffffff"}}) is False
    assert is_manual_context({"track_info": {"id": "deezer:1"}}) is False
    assert is_manual_context({"track_info": {"id": "", "_manual_metadata": True}}) is True
    assert is_manual_context({"_manual_metadata": True}) is True
    assert is_manual_context({"track_info": None}) is False
    assert is_manual_context(None) is False


# ── the switch ───────────────────────────────────────────────────────────────

def _client(db, monkeypatch):
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client(), mod


def test_the_route_turns_it_on_and_off_and_the_next_sync_looks_again(mdb, monkeypatch):
    pk = _mirror(mdb)
    c, mod = _client(mdb, monkeypatch)
    reset = []
    monkeypatch.setattr(mod, "_invalidate_mirror_sync_fingerprint", reset.append)
    assert unc.allowed_stub_ids(mdb) == frozenset()            # cached empty

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"allow_uncatalogued": True})
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["playlist"]["allow_uncatalogued"] is True
    assert unc.allowed_stub_ids(mdb) == frozenset({STUB})      # no half-minute wait
    assert reset == [pk]

    c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"allow_uncatalogued": True})
    assert reset == [pk]                                       # nothing changed, nothing reset

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"allow_uncatalogued": False})
    assert resp.status_code == 200 and unc.allowed_stub_ids(mdb) == frozenset()
    assert reset == [pk, pk]


@pytest.mark.parametrize("bad", ["maybe", 7, None, [], {}])
def test_the_route_takes_only_true_or_false(mdb, monkeypatch, bad):
    pk = _mirror(mdb)
    c, _mod = _client(mdb, monkeypatch)
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"allow_uncatalogued": bad})
    assert resp.status_code == 400
    assert mdb.get_mirrored_playlist(pk)["allow_uncatalogued"] is False


def test_another_setting_leaves_it_alone(mdb):
    pk = _mirror(mdb)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, allow_uncatalogued=True)
    mdb.update_mirrored_playlist_preferences(pk, profile_id=1, organize_by_playlist=True)
    assert mdb.get_mirrored_playlist(pk)["allow_uncatalogued"] is True
