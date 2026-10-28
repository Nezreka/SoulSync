"""#1859 (QT3496): tell same-named artists apart in folder names.

two musicbrainz artists called nirvana landed in one folder. the library
already holds musicbrainz's disambiguation, country and tags for enriched
artists; these tokens put them in the path. $albumartistclash only speaks
when another artist in the library has the name, so unique artists keep a
plain folder.
"""

from __future__ import annotations

import json

import pytest

import core.imports.artist_facts as facts_mod
import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.artist_facts import artist_facts
from core.imports.paths import apply_artist_facts, build_final_path_for_track
from database.music_database import MusicDatabase

US, UK, CULTURE = "mbid-us", "mbid-uk", "mbid-culture"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    rows = [
        ("1", "Nirvana", US, "dz-us", '["grunge"]'),
        ("2", "Nirvana", UK, "dz-uk", "[]"),
        ("3", "Culture", CULTURE, "dz-culture", '["reggae", "roots reggae"]'),
        ("4", "Nirvana", US, None, None),   # the same us band on a second server
    ]
    for artist_id, name, mbid, dz, genres in rows:
        conn.execute("INSERT INTO artists (id, name, musicbrainz_id, deezer_id, genres) "
                     "VALUES (?, ?, ?, ?, ?)", (artist_id, name, mbid, dz, genres))
    payloads = {
        US: {"disambiguation": "US grunge band", "country": "US"},
        UK: {"disambiguation": "UK 60s band", "country": "GB",
             "tags": [{"name": "psychedelic rock", "count": 3}]},
        CULTURE: {"area": {"iso-3166-1-codes": ["JM"]}},
    }
    for mbid, payload in payloads.items():
        conn.execute("INSERT INTO musicbrainz_cache (entity_type, entity_name, musicbrainz_id, "
                     "metadata_json) VALUES ('artist', 'x', ?, ?)", (mbid, json.dumps(payload)))
    conn.commit()
    conn.close()
    monkeypatch.setattr(facts_mod, "_get_db", lambda: db)
    facts_mod.clear_cache()
    yield db
    facts_mod.clear_cache()


def test_a_unique_artist_by_name(db):
    assert artist_facts("Culture") == {"disambiguation": "", "country": "JM",
                                       "genre": "Reggae", "clash": False}


def test_a_shared_name_needs_an_id(db):
    # by name alone the two nirvanas can't be told apart
    assert artist_facts("Nirvana")["disambiguation"] == ""
    us = artist_facts("Nirvana", source="deezer", source_id="dz-us")
    assert us == {"disambiguation": "US grunge band", "country": "US",
                  "genre": "Grunge", "clash": True}
    uk = artist_facts("Nirvana", mbid=UK)
    assert uk["disambiguation"] == "UK 60s band"
    assert uk["genre"] == "Psychedelic Rock"   # tags fill in for an empty genres list


def test_an_id_that_names_someone_else_is_ignored(db):
    # a compilation context can carry the album artist's id next to a track artist
    assert artist_facts("Culture", mbid=US)["country"] == "JM"
    assert artist_facts("Culture", source="deezer", source_id="dz-us")["country"] == "JM"


def test_an_unknown_artist_renders_nothing(db):
    assert artist_facts("Nobody") == {"disambiguation": "", "country": "",
                                      "genre": "", "clash": False}


def test_tokens_do_not_eat_albumartist(db):
    out = apply_artist_facts("$albumartist [$albumartistcountry - $albumartistgenre]/$artistinfo",
                             "Culture", "Culture", {})
    assert out == "$albumartist [JM - Reggae]/JM"


def test_clash_is_empty_for_a_unique_artist(db):
    assert apply_artist_facts("$albumartist ($albumartistclash)", "Culture", "Culture", {}) \
        == "$albumartist ()"


def test_composite_falls_back_through_country_and_genre():
    # QT3496's addon: the note when there is one, else country and genre together
    from core.imports.artist_facts import composite_chain
    assert composite_chain({"disambiguation": "film composer", "country": "US", "genre": "Score"}) \
        == "film composer"
    assert composite_chain({"disambiguation": "", "country": "US", "genre": "Rock"}) == "US Rock"
    assert composite_chain({"country": "UK", "genre": ""}) == "UK"
    assert composite_chain({"country": "", "genre": "Rock"}) == "Rock"
    assert composite_chain({}) == ""


def test_composite_token_renders_and_keeps_its_neighbours(db):
    # Culture: no note, JM + Reggae. info would say only "JM"
    out = apply_artist_facts("$albumartist [$albumartistcomposite]/$albumartistinfo/${artistcomposite}x",
                             "Culture", "Culture", {})
    assert out == "$albumartist [JM Reggae]/JM/JM Reggaex"
    # the uk nirvana has a note, so composite is just the note
    assert apply_artist_facts("[$albumartistcomposite]", "Nirvana", "Nirvana", {"_albumartist_mbid": UK}) \
        == "[UK 60s band]"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def template(monkeypatch, tmp_path):
    def use(album_path):
        config = _Config({
            "soulseek.transfer_path": str(tmp_path / "T"),
            "file_organization.enabled": True,
            "file_organization.templates": {"album_path": album_path},
            "file_organization.collab_artist_mode": "first",
            "file_organization.auto_disambiguation": False,
        })
        monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
        monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
        return tmp_path / "T"
    return use


def _import_destination(artist, artist_id, album):
    context = {
        "artist": {"name": artist, "id": artist_id},
        "album": {"name": album, "id": "alb", "release_date": "1991-09-24", "total_tracks": 12,
                  "total_discs": 1, "total_discs_declared": True, "album_type": "album",
                  "artists": [{"name": artist}]},
        "track_info": {"name": "Song", "id": "t", "track_number": 1, "disc_number": 1,
                       "artists": [{"name": artist}]},
        "original_search_result": {"title": "Song", "clean_title": "Song", "clean_album": album,
                                   "clean_artist": artist, "artists": [{"name": artist}]},
        "source": "deezer", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": artist, "id": artist_id},
        {"is_album": True, "album_name": album, "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False)
    return path


def test_composite_folder_through_the_path_builder(db, template):
    root = template("$albumartist [$albumartistcomposite]/$album/$track - $title")
    assert _import_destination("Culture", "dz-culture", "Two Sevens Clash") == \
        str(root / "Culture [JM Reggae]" / "Two Sevens Clash" / "01 - Song.flac")
    # nothing known: the empty brackets clean themselves up
    assert _import_destination("Nobody", "dz-none", "Debut") == \
        str(root / "Nobody" / "Debut" / "01 - Song.flac")


def test_two_nirvanas_get_two_folders_and_culture_stays_plain(db, template):
    root = template("$albumartist ($albumartistclash)/$album/$track - $title")
    assert _import_destination("Nirvana", "dz-us", "Nevermind") == \
        str(root / "Nirvana (US grunge band)" / "Nevermind" / "01 - Song.flac")
    assert _import_destination("Nirvana", "dz-uk", "Simopath") == \
        str(root / "Nirvana (UK 60s band)" / "Simopath" / "01 - Song.flac")
    assert _import_destination("Culture", "dz-culture", "Two Sevens Clash") == \
        str(root / "Culture" / "Two Sevens Clash" / "01 - Song.flac")


def test_reorganize_renders_what_the_import_rendered(db, template):
    template("$albumartist ($albumartistclash)/$album/$track - $title")
    imported = _import_destination("Nirvana", "dz-us", "Nevermind")
    ctx = lr._build_post_process_context(
        {"name": "Nevermind", "id": "alb", "release_date": "1991-09-24", "total_tracks": 12},
        {"name": "Song", "track_number": 1, "disc_number": 1, "artists": [{"name": "Nirvana"}]},
        "Nirvana", "Nevermind", 1, album_artist="Nirvana", local_artist_mbid=US)
    path, _ = build_final_path_for_track(
        ctx, ctx["spotify_artist"], lr._build_album_info(ctx), ".flac", create_dirs=False)
    assert path == imported


def test_web_server_template_copy_never_keeps_a_literal_token(db):
    import web_server
    out = web_server._apply_path_template(
        "$albumartist ($albumartistinfo)/$album/$title",
        {"artist": "Culture", "albumartist": "Culture", "album": "B", "title": "C",
         "track_number": 1, "disc_number": 1})
    assert "$" not in out
    assert out.startswith("Culture (JM)/")
