"""generated playlist cover art (pollinations).

what would quietly break: a refresh from the source wiping the cover the user
generated, jellyfin/navidrome libraries reading as empty because their ids
aren't numbers, the free tier's 45s limit turning into a wall of 402s, and the
prompt drifting into words the free model draws as people or lettering.
"""

import io
import json
import os
import types

import pytest
from flask import Flask, g

from core.playlists import cover_art as ca
from core.playlists import user_playlists as up
from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


@pytest.fixture(autouse=True)
def _fresh_throttle():
    ca._reset_throttle()
    yield
    ca._reset_throttle()


def _png(size=(300, 300)):
    # noise, not a flat color: a flat png squeezes under the "is this really
    # an image" size floor
    from PIL import Image
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, body=b"", ctype="image/jpeg", error=None):
        self.status_code = status
        self.content = body
        self.headers = {"content-type": ctype}
        self._error = error
        self.text = json.dumps({"error": {"message": error}}) if error else ""

    def json(self):
        if self._error is None:
            raise ValueError("not json")
        return {"error": {"message": self._error}}


class _Http:
    """records every call, answers from a queue (last answer repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [_Resp(body=_png())]
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _mirror(db, name="Night Drive", image="https://src/a.jpg", tracks=None, source="spotify"):
    tracks = tracks or [{"track_name": "One", "artist_name": "Artist", "source_track_id": "t1"}]
    return db.mirror_playlist(source, f"{source}-{name}", name, tracks, profile_id=1, image_url=image)


def _library(db, rows):
    """rows: (track_id, artist_name, artist_genres, album_genres, mood, year)."""
    with db._get_connection() as conn:
        for tid, artist, ar_genres, al_genres, mood, year in rows:
            conn.execute("INSERT OR IGNORE INTO artists (id, name, genres) VALUES (?, ?, ?)",
                         (f"ar-{artist}", artist, ar_genres))
            conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title, genres, year) "
                         "VALUES (?, ?, ?, ?, ?)", (f"al-{tid}", f"ar-{artist}", "Album", al_genres, year))
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, mood) VALUES (?, ?, ?, ?, ?)",
                         (tid, f"al-{tid}", f"ar-{artist}", f"Song {tid}", mood))
        conn.commit()


def _owned_track(title, artist, lib_id):
    return {"track_name": title, "artist_name": artist, "source_track_id": f"s-{title}",
            "extra_data": {"discovered": True, "library_track_id": lib_id}}


# ── the prompt ──────────────────────────────────────────────────────────


def test_electronic_does_not_read_as_techno():
    """'electro' (the techno scene) used to substring-match 'electronic', so an
    ambient playlist came back as laser beams."""
    prompt = ca.build_prompt({"name": "Selected", "genres": ["electronic", "ambient"], "moods": []})
    assert "mist" in prompt
    assert "laser" not in prompt
    # on its own, electronic is the flowing-forms scene, never the techno one
    prompt = ca.build_prompt({"name": "Selected", "genres": ["electronic"], "moods": []})
    assert "flowing forms" in prompt
    assert "laser" not in prompt


def test_the_name_beats_broad_genres():
    prompt = ca.build_prompt({"name": "Driving Music", "genres": ["rock", "pop", "r&b"], "moods": []})
    assert "highway" in prompt


def test_a_decade_in_the_name_sets_the_palette():
    assert "1970s" in ca.build_prompt({"name": "Time Machine — 1970s", "genres": [], "moods": []})
    assert "1980s" in ca.build_prompt({"name": "80s Bangers", "genres": [], "moods": []})
    assert ca._decade_from_name("2000s Throwbacks") == 2000
    assert ca._decade_from_name("Mix 2") is None


def test_prompt_never_negates_or_quotes():
    """the free model draws what a negation names, and quoted titles get
    lettered onto the image. both stay out of the prompt."""
    prompt = ca.build_prompt({"name": 'Mom\'s "Best" (2020)', "genres": ["pop"], "moods": ["happy"]})
    assert '"' not in prompt and "(" not in prompt and "'" not in prompt
    assert " no " not in f" {prompt.lower()} "
    assert "Mom s Best 2020" in prompt


def test_try_another_changes_the_art_style():
    vibe = {"name": "Mix", "genres": ["jazz"], "moods": []}
    styles = {ca.build_prompt(vibe, n).split(" of ")[0] for n in range(len(ca.MEDIUMS))}
    assert len(styles) == len(ca.MEDIUMS)


def test_no_scene_still_makes_a_prompt():
    prompt = ca.build_prompt({"name": "", "genres": [], "moods": []})
    assert prompt.startswith(ca.MEDIUMS[0])
    assert "atmospheric abstract scene" in prompt


# ── what the playlist sounds like ───────────────────────────────────────


def test_vibe_reads_jellyfin_style_text_ids(mdb):
    """library ids are text. jellyfin and navidrome use hex/base62, so parsing
    them as ints dropped every owned track and the prompt fell back to the
    name alone."""
    _library(mdb, [
        ("01487b14a9c94bc4", "Habn", '["ambient"]', '["electronic"]', "dreamy", 2021),
        ("0IOchBY6UeMfO9St", "Habn", '["ambient"]', '["electronic"]', "dreamy", 2022),
    ])
    pid = _mirror(mdb, tracks=[
        _owned_track("A", "Habn", "01487b14a9c94bc4"),
        _owned_track("B", "Habn", "0IOchBY6UeMfO9St"),
    ])
    vibe = ca.collect_vibe(mdb, mdb.get_mirrored_playlist(pid))
    assert vibe["genres"] == ["electronic"]
    assert vibe["moods"] == ["dreamy"]
    assert vibe["decade"] == 2020


def test_vibe_borrows_genres_from_known_artists(mdb):
    """a track that isn't in the library still says who made it. if the
    library knows that artist, its genres count, name matched any case."""
    _library(mdb, [("p1", "Moderat", '["techno", "electronica"]', "", None, None)])
    pid = _mirror(mdb, tracks=[
        {"track_name": "Bad Kingdom", "artist_name": "moderat", "source_track_id": "x1"},
        {"track_name": "Rusty Nails", "artist_name": "MODERAT, Someone", "source_track_id": "x2"},
    ])
    vibe = ca.collect_vibe(mdb, mdb.get_mirrored_playlist(pid))
    assert vibe["genres"][:2] == ["techno", "electronica"]


def test_vibe_drops_people_words_and_one_off_moods(mdb):
    _library(mdb, [
        ("1", "A", '["party", "female vocalists", "90s", "house"]', "", "excitable", None),
        ("2", "B", '["house"]', "", "euphoric", None),
        ("3", "C", '["house"]', "", "euphoric", None),
    ])
    pid = _mirror(mdb, tracks=[_owned_track(t, a, t) for t, a in (("1", "A"), ("2", "B"), ("3", "C"))])
    vibe = ca.collect_vibe(mdb, mdb.get_mirrored_playlist(pid))
    assert vibe["genres"] == ["house"]
    assert vibe["moods"] == ["euphoric"]


def test_vibe_decade_only_when_the_playlist_leans_one_way(mdb):
    def matched(year):
        return {"matched_data": {"album": {"release_date": f"{year}-01-01"}}}
    spread = [{"track_name": f"T{y}", "artist_name": "X", "source_track_id": f"s{y}",
               "extra_data": matched(y)} for y in (1971, 1984, 1995, 2003)]
    pid = _mirror(mdb, name="Spread", tracks=spread)
    assert ca.collect_vibe(mdb, mdb.get_mirrored_playlist(pid))["decade"] is None
    leaning = [{"track_name": f"T{y}", "artist_name": "X", "source_track_id": f"l{y}",
                "extra_data": matched(y)} for y in (1981, 1984, 1987, 2003)]
    pid = _mirror(mdb, name="Leaning", tracks=leaning)
    assert ca.collect_vibe(mdb, mdb.get_mirrored_playlist(pid))["decade"] == 1980


# ── pollinations ────────────────────────────────────────────────────────


def test_free_call_sends_the_negative_prompt_and_no_key():
    http = _Http()
    assert ca.generate_image("a lake", http_get=http, clock=_Clock()) == http.answers[0].content
    call = http.calls[0]
    assert call["url"].startswith(ca.FREE_URL)
    assert call["params"]["negative_prompt"] == ca.NEGATIVE_PROMPT
    assert "Authorization" not in call["headers"]


def test_keyed_call_uses_bearer_and_skips_the_free_throttle():
    http = _Http()
    clock = _Clock()
    ca.generate_image("a lake", api_key=" sk_abc ", http_get=http, clock=clock)
    ca.generate_image("a lake", api_key="sk_abc", http_get=http, clock=clock)
    assert len(http.calls) == 2
    assert all(c["url"].startswith(ca.KEYED_URL) for c in http.calls)
    assert http.calls[0]["headers"]["Authorization"] == "Bearer sk_abc"


def test_free_throttle_refuses_without_calling_out():
    """every profile on the box shares one ip, so the server keeps the 45s
    gap itself instead of collecting 402s."""
    http = _Http()
    clock = _Clock()
    ca.generate_image("a", http_get=http, clock=clock)
    clock.t += 20
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("b", http_get=http, clock=clock)
    assert err.value.status == 429
    assert err.value.retry_after == ca.FREE_SPACING_SECONDS - 20 + 1
    assert len(http.calls) == 1
    clock.t += 30
    ca.generate_image("c", http_get=http, clock=clock)
    assert len(http.calls) == 2


def test_free_402_reads_their_wait_and_holds_it():
    http = _Http(_Resp(402, ctype="application/json",
                       error="Free limit reached: 1 image every 45 seconds per IP. Retry after 27s, or ..."))
    clock = _Clock()
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("a", http_get=http, clock=clock)
    assert err.value.retry_after == 27
    clock.t += 10
    with pytest.raises(ca.CoverArtError):
        ca.generate_image("a", http_get=http, clock=clock)
    assert len(http.calls) == 1


def test_free_503_still_spends_the_slot():
    """live, a 503 that said "retry after 10s" was followed by a 402 saying
    31s: the busy answer counts against the ip all the same."""
    http = _Http(_Resp(503, ctype="application/json",
                       error="The Classic API is at its limit right now. Retry after 10s, or ..."))
    clock = _Clock()
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("a", http_get=http, clock=clock)
    assert err.value.status == 503
    assert err.value.retry_after >= ca.FREE_SPACING_SECONDS
    clock.t += 15
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("a", http_get=http, clock=clock)
    assert err.value.status == 429
    assert len(http.calls) == 1


def test_a_rejected_key_says_so():
    http = _Http(_Resp(401, ctype="application/json", error="A valid API key is required."))
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("a", api_key="bad", http_get=http, clock=_Clock())
    assert "key" in str(err.value).lower()
    assert err.value.status == 400


@pytest.mark.parametrize("resp", [
    _Resp(200, body=b'{"ok": true}', ctype="application/json"),
    _Resp(200, body=b"tiny", ctype="image/jpeg"),
    _Resp(500, ctype="text/html"),
])
def test_anything_but_an_image_is_an_error(resp):
    with pytest.raises(ca.CoverArtError):
        ca.generate_image("a", api_key="k", http_get=_Http(resp), clock=_Clock())


def test_network_failure_is_a_readable_error():
    def boom(*a, **k):
        raise OSError("connection reset")
    with pytest.raises(ca.CoverArtError) as err:
        ca.generate_image("a", api_key="k", http_get=boom, clock=_Clock())
    assert "Pollinations" in str(err.value)


# ── the settings test button ────────────────────────────────────────────

def test_key_check_asks_about_the_key_and_spends_nothing():
    http = _Http(_Resp(status=200, ctype="application/json"))
    assert ca.check_key("sk", http_get=http) == (True, "Pollinations accepted the key.")
    assert http.calls[0]["url"] == ca.KEY_CHECK_URL
    assert http.calls[0]["headers"] == {"Authorization": "Bearer sk"}
    # never the image endpoint, never the free tier's one slot
    assert ca._next_free_at == 0


@pytest.mark.parametrize("status,words", [(401, "didn't accept"), (403, "image access"), (500, "500")])
def test_key_check_says_what_went_wrong(status, words):
    ok, msg = ca.check_key("sk", http_get=_Http(_Resp(status=status)))
    assert not ok and words in msg


def test_no_key_only_checks_pollinations_answers():
    http = _Http(_Resp(status=200, ctype="application/json"))
    ok, msg = ca.check_key("  ", http_get=http)
    assert ok and "45 seconds" in msg
    assert http.calls[0]["url"] == ca.MODELS_URL
    assert http.calls[0]["headers"] == {}


def test_key_check_network_failure_is_readable():
    def boom(*a, **k):
        raise OSError("connection reset")
    assert ca.check_key("sk", http_get=boom) == (False, "Couldn't reach Pollinations.")


def test_the_settings_button_routes_to_the_key_check(monkeypatch):
    from unittest.mock import patch
    from core import connection_test
    seen = []
    monkeypatch.setattr(ca, "check_key", lambda key: seen.append(key) or (True, "ok"))
    with patch.object(connection_test.config_manager, "get",
                      side_effect=lambda k, d=None: "sk" if k == "pollinations.api_key" else {}), \
         patch.object(connection_test.config_manager, "set"):
        assert connection_test.run_service_test("pollinations", {}) == (True, "ok")
    assert seen == ["sk"]


# ── preview, apply, restore ─────────────────────────────────────────────


def test_preview_leaves_the_playlist_alone(mdb, tmp_path):
    pid = _mirror(mdb)
    pl = mdb.get_mirrored_playlist(pid)
    out = ca.make_preview(mdb, pl, base_dir=str(tmp_path), http_get=_Http())
    assert out["preview_url"].startswith(f"/api/mirrored-playlists/{pid}/cover-art/preview?v=")
    assert out["keyed"] is False
    assert os.path.isfile(ca.preview_path(pid, str(tmp_path)))
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] == "https://src/a.jpg"
    assert row["custom_cover"] is False


def test_applied_cover_survives_a_refresh_from_the_source(mdb, tmp_path):
    """the source sends its own poster on every refresh. the generated cover
    has to stay put, and the source's latest poster is kept for restore."""
    pid = _mirror(mdb)
    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path), http_get=_Http())
    url = ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    assert url.startswith(f"/api/user-playlists/{pid}/cover?v=")
    assert not os.path.exists(ca.preview_path(pid, str(tmp_path), mkdir=False))
    assert os.path.isfile(up.cover_path(pid, str(tmp_path)))

    _mirror(mdb, image="https://src/b.jpg")  # refresh
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] == url
    assert row["custom_cover"] is True
    assert row["source_image_url"] == "https://src/b.jpg"


def test_a_second_generate_keeps_the_real_original(mdb, tmp_path):
    pid = _mirror(mdb)
    for _ in range(2):
        ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path),
                        api_key="k", http_get=_Http())
        ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    assert mdb.get_mirrored_playlist(pid)["source_image_url"] == "https://src/a.jpg"


def test_restore_goes_back_and_refreshes_write_again(mdb, tmp_path):
    pid = _mirror(mdb)
    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path), http_get=_Http())
    ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    _mirror(mdb, image="https://src/b.jpg")

    ca.restore_original(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] == "https://src/b.jpg"
    assert row["custom_cover"] is False
    assert row["source_image_url"] is None
    assert not os.path.exists(up.cover_path(pid, str(tmp_path), mkdir=False))

    _mirror(mdb, image="https://src/c.jpg")
    assert mdb.get_mirrored_playlist(pid)["image_url"] == "https://src/c.jpg"


def test_user_playlist_cover_is_just_its_cover(mdb, tmp_path):
    """a user playlist has no source to protect it from, so no custom flag:
    the same as an uploaded cover."""
    pid = up.create_playlist(mdb, 1, "Mine")
    pl = mdb.get_mirrored_playlist(pid)
    ca.make_preview(mdb, pl, base_dir=str(tmp_path), http_get=_Http())
    url = ca.apply_preview(mdb, pl, base_dir=str(tmp_path))
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] == url
    assert row["custom_cover"] is False


def test_apply_without_a_preview_is_refused(mdb, tmp_path):
    pid = _mirror(mdb)
    with pytest.raises(ca.CoverArtError) as err:
        ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    assert err.value.status == 404
    assert mdb.get_mirrored_playlist(pid)["image_url"] == "https://src/a.jpg"


def test_a_bad_image_from_pollinations_saves_nothing(mdb, tmp_path):
    pid = _mirror(mdb)
    junk = _Resp(200, body=b"\xff\xd8" + b"x" * 5000, ctype="image/jpeg")
    with pytest.raises(ca.CoverArtError):
        ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path),
                        api_key="k", http_get=_Http(junk))
    assert not os.path.exists(ca.preview_path(pid, str(tmp_path), mkdir=False))


def test_deleting_the_playlist_takes_the_preview_too(mdb, tmp_path):
    pid = _mirror(mdb)
    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path), http_get=_Http())
    up.remove_cover_file(pid, str(tmp_path))
    assert not os.path.exists(ca.preview_path(pid, str(tmp_path), mkdir=False))


# ── the routes ──────────────────────────────────────────────────────────


def _client(db, monkeypatch, tmp_path, *, profile_id=1, is_admin=True, api_key=""):
    import api.mirrored_playlists as mp
    import api.user_playlists as upm

    cfg = types.SimpleNamespace(
        base_dir=str(tmp_path),
        get=lambda key, default=None: api_key if key == "pollinations.api_key" else default,
    )
    for mod in (mp, upm):
        monkeypatch.setattr(mod, "get_database", lambda: db)
        monkeypatch.setattr(mod, "config_manager", cfg)
    monkeypatch.setattr(mp, "get_current_profile_id", lambda: profile_id)
    monkeypatch.setattr(upm, "get_current_profile_id", lambda: profile_id)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = is_admin

    app.register_blueprint(mp.create_blueprint())
    app.register_blueprint(upm.create_blueprint())
    return app.test_client()


@pytest.fixture()
def http(monkeypatch):
    fake = _Http()
    monkeypatch.setattr("requests.get", fake)
    return fake


def test_generate_preview_apply_serve_restore(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path)

    # nothing generated yet: the shared cover route doesn't serve a mirror
    assert c.get(f"/api/user-playlists/{pid}/cover").status_code == 404

    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={"attempt": 2})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["prompt"].startswith(ca.MEDIUMS[2])
    r = c.get(body["preview_url"])
    assert r.status_code == 200 and r.content_type == "image/webp"

    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art/apply")
    assert r.status_code == 200, r.data
    image_url = r.get_json()["image_url"]
    r = c.get(image_url)
    assert r.status_code == 200 and r.content_type == "image/webp"

    assert c.delete(f"/api/mirrored-playlists/{pid}/cover-art").status_code == 200
    assert mdb.get_mirrored_playlist(pid)["image_url"] == "https://src/a.jpg"
    assert c.get(f"/api/user-playlists/{pid}/cover").status_code == 404


def test_the_key_in_settings_is_used(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path, api_key="sk_live")
    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={})
    assert r.status_code == 200, r.data
    assert r.get_json()["keyed"] is True
    assert http.calls[0]["headers"]["Authorization"] == "Bearer sk_live"


def test_throttled_generate_answers_429_with_the_wait(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path)
    assert c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={}).status_code == 200
    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={})
    assert r.status_code == 429
    assert r.get_json()["retry_after"] > 0
    assert len(http.calls) == 1


def test_another_profiles_playlist_reads_as_missing(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)  # profile 1's
    c = _client(mdb, monkeypatch, tmp_path, profile_id=2, is_admin=False)
    assert c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={}).status_code == 404
    assert c.post(f"/api/mirrored-playlists/{pid}/cover-art/apply").status_code == 404
    assert c.delete(f"/api/mirrored-playlists/{pid}/cover-art").status_code == 404
    assert http.calls == []


def test_restore_with_nothing_generated_is_refused(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path)
    assert c.delete(f"/api/mirrored-playlists/{pid}/cover-art").status_code == 400
    # but throwing away a preview always works
    assert c.delete(f"/api/mirrored-playlists/{pid}/cover-art?preview_only=1").status_code == 200
    assert mdb.get_mirrored_playlist(pid)["image_url"] == "https://src/a.jpg"


def test_a_refresh_echoing_the_cover_back_keeps_the_real_original(mdb, tmp_path):
    """reported oct 25 on a spotify_public mirror: its source sends no poster,
    so the scheduled refresh passes the row's own image_url back in. that was
    the generated cover, and it got stashed as the "original", so restore
    would have put the generated cover back (and then deleted its file)."""
    pid = _mirror(mdb, source="spotify_public")
    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path), http_get=_Http())
    url = ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    # refresh_mirrored: image_url=pl.get('image_url')
    _mirror(mdb, source="spotify_public", image=mdb.get_mirrored_playlist(pid)["image_url"])
    # an older generated url coming back the same way
    _mirror(mdb, source="spotify_public", image=f"/api/user-playlists/{pid}/cover?v=1")
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] == url
    assert row["source_image_url"] == "https://src/a.jpg"
    ca.restore_original(mdb, row, base_dir=str(tmp_path))
    assert mdb.get_mirrored_playlist(pid)["image_url"] == "https://src/a.jpg"


def test_restore_from_an_already_stashed_echo_goes_to_no_cover(mdb, tmp_path):
    """rows saved before the echo fix hold the generated url as the original.
    restoring them must not point at the file restore is about to delete."""
    pid = _mirror(mdb, source="spotify_public")
    local = f"/api/user-playlists/{pid}/cover?v=5"
    with mdb._get_connection() as conn:
        conn.execute("UPDATE mirrored_playlists SET custom_cover = 1, image_url = ?, "
                     "source_image_url = ? WHERE id = ?", (local, local, pid))
        conn.commit()
    ca.restore_original(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    row = mdb.get_mirrored_playlist(pid)
    assert row["image_url"] is None
    assert row["custom_cover"] is False


# ── the name on the cover ───────────────────────────────────────────────


def _cover_pixels(data):
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGB")


def _region_diff(a, b, box):
    from PIL import ImageChops, ImageStat
    return max(ImageStat.Stat(ImageChops.difference(a.crop(box), b.crop(box))).mean)


def test_the_name_goes_on_top_and_leaves_the_mark_corner_alone(mdb, tmp_path):
    """the name sits top-left on a fade. the bottom of the image, where the
    free tier's mark is, comes through untouched."""
    pid = _mirror(mdb, source="tidal")
    out = ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path), http_get=_Http())
    assert out["show_name"] is True
    with open(ca.raw_preview_path(pid, str(tmp_path)), "rb") as fh:
        raw = _cover_pixels(fh.read())
    with open(ca.preview_path(pid, str(tmp_path)), "rb") as fh:
        named = _cover_pixels(fh.read())
    side = raw.width
    assert _region_diff(raw, named, (0, 0, side, side // 4)) > 20  # name + fade
    # bottom quarter: only the webp re-encode, nothing drawn
    assert _region_diff(raw, named, (0, side * 3 // 4, side, side)) < 4


def test_show_name_off_is_the_image_as_made(mdb, tmp_path):
    pid = _mirror(mdb)
    out = ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path),
                          show_name=False, http_get=_Http())
    assert out["show_name"] is False
    with open(ca.raw_preview_path(pid, str(tmp_path)), "rb") as a, \
            open(ca.preview_path(pid, str(tmp_path)), "rb") as b:
        assert a.read() == b.read()


def test_the_small_line_names_the_source():
    assert ca.source_label({"source": "tidal"}) == "Tidal playlist"
    assert ca.source_label({"source": "spotify_public"}) == "Spotify playlist"
    assert ca.source_label({"source": "metasync"}) == "SoulSync Charts"
    assert ca.source_label({"source": "something-new"}) == "Playlist"


def test_a_user_playlist_is_a_soulsync_playlist(mdb):
    pid = up.create_playlist(mdb, 1, "Mine")
    assert ca.source_label(mdb.get_mirrored_playlist(pid)) == "SoulSync playlist"


@pytest.mark.parametrize("name", [
    "Driving Music",
    "The Absolutely Enormous Collection of Every Song I Have Ever Loved In My Entire Life Ever",
    "Supercalifragilisticexpialidocious" * 2,
    "",
])
def test_any_name_fits_in_three_lines(name):
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGB", (up.COVER_SIDE, up.COVER_SIDE)))
    width = up.COVER_SIDE * 0.875
    font, lines, _ = ca._fit_title(draw, name or "Playlist", up.COVER_SIDE, width)
    assert 1 <= len(lines) <= 3
    assert all(draw.textlength(line, font=font) <= width for line in lines)


def test_the_fonts_ship_with_soulsync():
    for font in (ca.TITLE_FONT, ca.LABEL_FONT):
        assert os.path.isfile(os.path.join(ca.FONT_DIR, font))


def test_used_or_thrown_away_takes_the_raw_copy_too(mdb, tmp_path):
    pid = _mirror(mdb)
    # keyed: two generates back to back would hit the free tier's 45s
    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path),
                    api_key="k", http_get=_Http())
    ca.discard_preview(pid, str(tmp_path))
    assert not os.path.exists(ca.raw_preview_path(pid, str(tmp_path), mkdir=False))

    ca.make_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path),
                    api_key="k", http_get=_Http())
    ca.apply_preview(mdb, mdb.get_mirrored_playlist(pid), base_dir=str(tmp_path))
    assert not os.path.exists(ca.raw_preview_path(pid, str(tmp_path), mkdir=False))


def test_name_switch_redraws_without_a_new_image(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path)
    assert c.post(f"/api/mirrored-playlists/{pid}/cover-art", json={"show_name": True}).status_code == 200
    asked = len(http.calls)

    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art/name", json={"show_name": False})
    assert r.status_code == 200, r.data
    assert r.get_json()["show_name"] is False
    assert len(http.calls) == asked  # no pollinations call, so no 45s spent
    with open(ca.raw_preview_path(pid, str(tmp_path)), "rb") as a, \
            open(ca.preview_path(pid, str(tmp_path)), "rb") as b:
        assert a.read() == b.read()

    assert c.post(f"/api/mirrored-playlists/{pid}/cover-art/name",
                  json={"show_name": True}).get_json()["show_name"] is True
    with open(ca.raw_preview_path(pid, str(tmp_path)), "rb") as a, \
            open(ca.preview_path(pid, str(tmp_path)), "rb") as b:
        assert a.read() != b.read()


def test_name_switch_without_a_preview_says_so(mdb, monkeypatch, tmp_path, http):
    pid = _mirror(mdb)
    c = _client(mdb, monkeypatch, tmp_path)
    r = c.post(f"/api/mirrored-playlists/{pid}/cover-art/name", json={"show_name": False})
    assert r.status_code == 404
    assert "Generate a new one" in r.get_json()["error"]
