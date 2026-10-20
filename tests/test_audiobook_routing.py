"""Artists routed to the audiobook library: where their downloads go, and that
the sync does not fetch them again because the media server never sees them."""

from __future__ import annotations

import pytest

from core import audiobook_routing as routing
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


@pytest.fixture
def folders(tmp_path, monkeypatch):
    transfer = tmp_path / "Transfer"
    books = tmp_path / "audiobooks"
    transfer.mkdir()
    books.mkdir()
    monkeypatch.setattr("core.imports.paths.shared_transfer_root", lambda: str(transfer))
    monkeypatch.setattr(routing, "audiobook_root", lambda: str(books))
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: True)
    return transfer, books


def _context(artist, guests=()):
    return {
        "artist": {"name": artist},
        "track_info": {"artists": [{"name": artist}, *({"name": g} for g in guests)]},
    }


def test_route_key_ignores_case_and_punctuation():
    assert routing.route_key("Bobby & Max") == routing.route_key("bobby  &  MAX")
    assert routing.route_key("") == ""
    assert routing.route_key(None) == ""


def test_add_list_remove_roundtrip(db):
    assert routing.add_route("Die fantastische Fibi", db)
    assert routing.add_route("die fantastische fibi", db)  # same key, label refreshed
    routes = routing.list_routes(db)
    assert [r["label"] for r in routes] == ["die fantastische fibi"]
    assert routing.is_routed_artist("Die Fantastische Fibi", db)
    assert routing.remove_route("Die fantastische Fibi", db)
    assert not routing.is_routed_artist("Die fantastische Fibi", db)
    assert not routing.remove_route("Die fantastische Fibi", db)


def test_blank_names_are_not_routed(db):
    assert not routing.add_route("   ", db)
    assert not routing.is_routed_artist("", db)


def test_routed_artist_files_into_the_audiobook_folder(db, folders):
    _, books = folders
    routing.add_route("Bobby & Max", db)
    assert routing.routed_root_for_context(_context("Bobby & Max"), db) == str(books)


def test_unrouted_artist_keeps_the_music_library(db, folders):
    routing.add_route("Bobby & Max", db)
    assert routing.routed_root_for_context(_context("Queen"), db) is None


def test_only_the_primary_artist_decides(db, folders):
    routing.add_route("Bobby & Max", db)
    # a routed artist as guest on a song must not pull the song into the books
    assert routing.routed_root_for_context(_context("Queen", guests=["Bobby & Max"]), db) is None
    # a guest on a routed artist's episode does not undo the routing
    assert routing.routed_root_for_context(_context("Bobby & Max", guests=["Queen"]), db) is not None


def test_context_without_artist_is_untouched(db, folders):
    routing.add_route("Bobby & Max", db)
    assert routing.routed_root_for_context({}, db) is None
    assert routing.routed_root_for_context(None, db) is None


def test_lookup_failure_never_breaks_an_import(folders):
    class Broken:
        def has_audiobook_route(self, *_):
            raise RuntimeError("db locked")

    assert routing.routed_root_for_context(_context("Bobby & Max"), Broken()) is None


def test_existing_download_path_follows_a_hand_moved_folder(folders):
    transfer, books = folders
    moved = books / "Bobby & Max" / "Bobby & Max - Folge 84" / "01 - Teil 1.mp3"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(b"x")
    recorded = transfer / "Bobby & Max" / "Bobby & Max - Folge 84" / "01 - Teil 1.mp3"
    assert routing.existing_download_path(str(recorded)) == str(moved)
    assert routing.existing_download_path(str(books / "nope.mp3")) is None
    assert routing.existing_download_path("") is None


def _record_download(db, path, title, artist, album="Folge 84"):
    with db._get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO track_downloads (file_path, source_service, track_title, "
            "track_artist, track_album, status) VALUES (?, 'youtube', ?, ?, ?, 'completed')",
            (str(path), title, artist, album))
        conn.commit()


def test_routed_download_counts_as_owned(db, folders):
    _, books = folders
    routing.add_route("Bobby & Max", db)
    episode = books / "Bobby & Max" / "Folge 84" / "01 - Fibis Ferienresort.mp3"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"x")
    _record_download(db, episode, "Fibis Ferienresort", "Bobby & Max")

    track, confidence = db.check_track_exists("Fibis Ferienresort", "Bobby & Max", include_downloads=True)
    assert track is not None and confidence >= 0.8
    assert track.file_path == str(episode)


def test_deleted_file_is_no_longer_owned(db, folders):
    _, books = folders
    routing.add_route("Bobby & Max", db)
    _record_download(db, books / "gone.mp3", "Fibis Ferienresort", "Bobby & Max")
    track, _ = db.check_track_exists("Fibis Ferienresort", "Bobby & Max", include_downloads=True)
    assert track is None


def test_unrouted_artist_download_is_not_owned_by_the_ledger(db, folders):
    _, books = folders
    episode = books / "Queen" / "song.mp3"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"x")
    _record_download(db, episode, "Bohemian Rhapsody", "Queen")
    track, _ = db.check_track_exists("Bohemian Rhapsody", "Queen", include_downloads=True)
    assert track is None


def test_transfer_root_for_context_prefers_the_route(db, folders, monkeypatch):
    from core.imports import paths

    transfer, books = folders
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    routing.add_route("Bobby & Max", db)
    assert paths.transfer_root_for_context(_context("Bobby & Max")) == str(books)
    assert paths.transfer_root_for_context(_context("Queen")) == str(transfer)


def test_routes_api_roundtrip(db, monkeypatch):
    from flask import Flask

    from api.audiobook_routes import create_blueprint

    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    client = app.test_client()

    assert client.post("/api/audiobook-routes", json={"name": "  "}).status_code == 400
    assert client.post("/api/audiobook-routes", json={"name": "Bobby & Max"}).get_json()["success"]
    listed = client.get("/api/audiobook-routes").get_json()["artists"]
    assert [a["label"] for a in listed] == ["Bobby & Max"]
    assert client.delete("/api/audiobook-routes/Bobby & Max").get_json()["removed"] is True
    assert client.get("/api/audiobook-routes").get_json()["artists"] == []


def test_routed_download_is_filed_under_the_audiobook_folder_with_the_usual_layout(
        db, folders, monkeypatch):
    """The real path builder: only the root changes, Artist/Album/NN - Title stays."""
    import core.imports.paths as import_paths

    _, books = folders

    class _Config:
        _values = {
            "soulseek.transfer_path": "./Transfer",
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": "$albumartist/$album/$track - $title",
                "single_path": "$albumartist/$albumartist - $title/$title",
            },
            "file_organization.collab_artist_mode": "first",
        }

        def get(self, key, default=None):
            return self._values.get(key, default)

        def get_active_media_server(self):
            return "primary"

    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config())
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    routing.add_route("Bobby & Max", db)

    artist, album = "Bobby & Max", "Folge 84: Fibis verrücktes Ferienresort"

    def destination(who):
        context = {
            "artist": {"name": who},
            "album": {"name": album, "id": "a1", "release_date": "2026-01-01",
                      "total_tracks": 8, "album_type": "album", "artists": [{"name": who}]},
            "track_info": {"name": "Teil 02", "id": "t", "track_number": 2,
                           "disc_number": 1, "artists": [{"name": who}]},
            "original_search_result": {"title": "Teil 02", "clean_title": "Teil 02",
                                       "clean_album": album, "clean_artist": who,
                                       "artists": [{"name": who}]},
            "source": "deezer", "is_album_download": True,
        }
        path, _ = import_paths.build_final_path_for_track(
            context, {"name": who},
            {"is_album": True, "album_name": album, "track_number": 2, "disc_number": 1},
            ".mp3", create_dirs=False)
        return path

    routed = destination(artist)
    assert routed.startswith(str(books) + "/"), routed
    assert "/Bobby & Max/" in routed and routed.endswith("02 - Teil 02.mp3"), routed
    assert not destination("Queen").startswith(str(books)), "unrouted artists keep the music library"


def _album(root, artist, album, files=("01 - Teil 1.mp3", "02 - Teil 2.mp3")):
    folder = root / artist / album
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"x")
    return folder


def test_move_preview_changes_nothing(folders):
    transfer, books = folders
    _album(transfer, "Bobby & Max", "Folge 84")
    result = routing.move_existing("bobby & max")
    assert [i["action"] for i in result["items"]] == ["move"]
    assert result["applied"] is False and result["moved"] == 0
    assert (transfer / "Bobby & Max" / "Folge 84" / "01 - Teil 1.mp3").exists()
    assert not (books / "Bobby & Max").exists()


def test_move_apply_relocates_albums_and_tidies_up(folders):
    transfer, books = folders
    _album(transfer, "Bobby & Max", "Folge 84")
    _album(transfer, "Bobby & Max", "Folge 85")
    _album(transfer, "Queen", "Jazz", files=("01 - Mustapha.mp3",))
    result = routing.move_existing("Bobby & Max", apply=True)
    assert result["moved"] == 2 and result["failed"] == 0
    assert (books / "Bobby & Max" / "Folge 84" / "02 - Teil 2.mp3").exists()
    assert not (transfer / "Bobby & Max").exists(), "emptied artist folder is removed"
    assert (transfer / "Queen" / "Jazz" / "01 - Mustapha.mp3").exists(), "other artists untouched"


def test_move_never_overwrites(folders):
    transfer, books = folders
    _album(transfer, "Bobby & Max", "Folge 84", files=("01 - Teil 1.mp3",))
    existing = _album(books, "Bobby & Max", "Folge 84", files=("01 - Teil 1.mp3",))
    (existing / "01 - Teil 1.mp3").write_bytes(b"keep me")
    result = routing.move_existing("Bobby & Max", apply=True)
    assert result["moved"] == 0 and result["skipped"] == 1
    assert (existing / "01 - Teil 1.mp3").read_bytes() == b"keep me"
    assert (transfer / "Bobby & Max" / "Folge 84" / "01 - Teil 1.mp3").exists()


def test_moved_albums_stay_owned_through_the_ledger(db, folders):
    transfer, books = folders
    routing.add_route("Bobby & Max", db)
    folder = _album(transfer, "Bobby & Max", "Folge 84", files=("01 - Fibis Ferienresort.mp3",))
    _record_download(db, folder / "01 - Fibis Ferienresort.mp3", "Fibis Ferienresort", "Bobby & Max")
    routing.move_existing("Bobby & Max", apply=True)
    track, _ = db.check_track_exists("Fibis Ferienresort", "Bobby & Max", include_downloads=True)
    assert track is not None and track.file_path.startswith(str(books))


def test_move_api(db, folders, monkeypatch):
    from flask import Flask

    from api.audiobook_routes import create_blueprint

    transfer, books = folders
    _album(transfer, "Bobby & Max", "Folge 84")
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    client = app.test_client()
    preview = client.post("/api/audiobook-routes/Bobby & Max/move", json={}).get_json()
    assert preview["applied"] is False and preview["items"][0]["action"] == "move"
    done = client.post("/api/audiobook-routes/Bobby & Max/move", json={"apply": True}).get_json()
    assert done["moved"] == 1 and (books / "Bobby & Max" / "Folge 84").is_dir()


def test_check_reports_which_names_are_routed(db):
    from flask import Flask

    from api.audiobook_routes import create_blueprint

    routing.add_route("Bobby & Max", db)
    assert routing.routed_names(["Queen", "bobby  &  MAX", "", "Bobby & Max"], db) == ["bobby  &  MAX", "Bobby & Max"]
    assert routing.routed_names([], db) == []
    assert routing.routed_names(["", "  "], db) == []


# ---------------------------------------------------------------------------
# metadata.opf
# ---------------------------------------------------------------------------

def _tagged_mp3(path, **tags):
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    ID3().save(str(path))
    easy = EasyID3(str(path))
    easy.update(tags)
    easy.save()
    return path


def _episode(root, artist, album, *, tracks=2, date="2026-09-18"):
    folder = root / artist / f"{artist} - {album.replace(':', '_')}"
    for number in range(1, tracks + 1):
        _tagged_mp3(folder / f"{number:02d} - Teil {number}.mp3",
                    albumartist=artist, artist=artist, album=album, date=date)
    return folder


def test_series_and_episode_come_from_the_album_tags(tmp_path):
    folder = _episode(tmp_path, "Die fantastische Fibi", "Folge 16: Die finstere Entführung")
    book = routing.album_book_from_folder(folder)
    assert book == {
        "title": "Folge 16: Die finstere Entführung",
        "authors": ["Die fantastische Fibi"],
        "series": [{"title": "Die fantastische Fibi", "sequence": "16"}],
        "release_date": "2026-09-18",
    }


@pytest.mark.parametrize("album, sequence", [
    ("Superfolge: Angriff auf die Zauberschule", ""),
    ("Hörbuch: Der mysteriöse Fremde (ungekürzt)", ""),
    ("episode 3 - Anfang", "3"),
    ("Folge 100: Jubiläum", "100"),
])
def test_only_a_leading_episode_number_counts(tmp_path, album, sequence):
    book = routing.album_book_from_folder(_episode(tmp_path, "Bobby & Max", album, tracks=1))
    assert book["series"][0]["sequence"] == sequence


def test_a_folder_without_tagged_artist_or_audio_gets_no_book(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert routing.album_book_from_folder(empty) is None
    nameless = tmp_path / "nameless"
    _tagged_mp3(nameless / "01.mp3", album="Folge 1: x")
    assert routing.album_book_from_folder(nameless) is None
    assert routing.write_album_sidecars(nameless) is False
    assert not (nameless / "metadata.opf").exists()


def test_opf_carries_author_series_and_position(tmp_path):
    folder = _episode(tmp_path, "Die fantastische Fibi", "Folge 16: Die finstere Entführung")
    assert routing.write_album_sidecars(folder) is True
    opf = (folder / "metadata.opf").read_text(encoding="utf-8")
    assert "<dc:title>Folge 16: Die finstere Entführung</dc:title>" in opf
    assert 'opf:role="aut"' in opf and "Die fantastische Fibi</dc:creator>" in opf
    assert '<meta name="calibre:series" content="Die fantastische Fibi"/>' in opf
    assert '<meta name="calibre:series_index" content="16"/>' in opf
    assert "<dc:date>2026-09-18</dc:date>" in opf


def test_opf_is_not_rewritten_when_nothing_changed(tmp_path):
    import os

    folder = _episode(tmp_path, "Bobby & Max", "Folge 93: Dämonen aus der Unterwelt")
    assert routing.write_album_sidecars(folder) is True
    opf = folder / "metadata.opf"
    os.utime(opf, (1_000_000, 1_000_000))
    assert routing.write_album_sidecars(folder) is False
    assert opf.stat().st_mtime == 1_000_000, "an identical OPF must keep its mtime"
    assert not (folder / "metadata.opf.tmp").exists()


def test_xml_special_characters_are_escaped(tmp_path):
    folder = _episode(tmp_path, "Bobby & Max", "Folge 7: <Gefahr> & Mut", tracks=1)
    routing.write_album_sidecars(folder)
    opf = (folder / "metadata.opf").read_text(encoding="utf-8")
    import xml.etree.ElementTree as ET

    ET.fromstring(opf)  # still well-formed
    assert "&lt;Gefahr&gt; &amp; Mut" in opf


def test_an_import_writes_the_sidecar_only_inside_the_audiobook_folder(folders):
    transfer, books = folders
    routed = _episode(books, "Bobby & Max", "Folge 84: Fibis verrücktes Ferienresort")
    music = _episode(transfer, "Queen", "Folge 1: Jazz", tracks=1)

    assert routing.write_album_sidecars_for_file(routed / "01 - Teil 1.mp3") is True
    assert (routed / "metadata.opf").exists()
    assert routing.write_album_sidecars_for_file(music / "01 - Teil 1.mp3") is False
    assert not (music / "metadata.opf").exists(), "music imports are untouched"
    assert routing.write_album_sidecars_for_file("") is False


def test_refresh_covers_only_that_artists_albums(folders):
    _, books = folders
    _episode(books, "Bobby & Max", "Folge 84: Fibis verrücktes Ferienresort")
    _episode(books, "Bobby & Max", "Folge 85: Das Rätsel der Schlange")
    other = _episode(books, "Lisa & Fiona", "Folge 1: Das verzauberte Einhorn")
    hidden = _episode(books / ".deleted", "Bobby & Max", "Folge 99: Weg", tracks=1)

    assert routing.refresh_sidecars("bobby & max") == {"written": 2, "unchanged": 0}
    assert routing.refresh_sidecars("Bobby & Max") == {"written": 0, "unchanged": 2}
    assert not (other / "metadata.opf").exists()
    assert not (hidden / "metadata.opf").exists()


def test_moved_albums_get_their_sidecar(folders):
    transfer, books = folders
    _episode(transfer, "Bobby & Max", "Folge 84: Fibis verrücktes Ferienresort")
    routing.move_existing("Bobby & Max", apply=True)
    moved = books / "Bobby & Max" / "Bobby & Max - Folge 84_ Fibis verrücktes Ferienresort"
    assert (moved / "metadata.opf").is_file()


def test_refresh_api(folders):
    from flask import Flask

    from api.audiobook_routes import create_blueprint

    _, books = folders
    _episode(books, "Bobby & Max", "Folge 84: Fibis verrücktes Ferienresort")
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    reply = app.test_client().post("/api/audiobook-routes/Bobby & Max/refresh").get_json()
    assert reply == {"success": True, "written": 1, "unchanged": 0}


# ---------------------------------------------------------------------------
# genre
# ---------------------------------------------------------------------------

def _tag_flac(tmp_path, monkeypatch, db, artist, *, routed):
    """Run the real tag writer on a tiny FLAC whose source says the genre is Rock."""
    from unittest.mock import patch

    pytest.importorskip("mutagen")
    from mutagen.flac import FLAC

    import core.metadata.enrichment as enrichment

    path = str(tmp_path / "t.flac")
    with open(path, "wb") as f:
        f.write(b"fLaC" + b"\x80\x00\x00\x22" + b"\x00\x10\x00\x10" + b"\x00" * 6
                + b"\x0a\xc4\x42\xf0" + b"\x00" * 20)
    FLAC(path).save()
    if routed:
        routing.add_route(artist, db)
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    monkeypatch.setattr(routing, "audiobook_root", lambda: str(tmp_path / "audiobooks"))
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: True)
    meta = {"title": "Kapitel 1", "artist": artist, "album_artist": artist, "album": "Folge 1",
            "track_number": 1, "total_tracks": 2, "disc_number": 1, "genre": "Rock"}

    class _Cfg:
        def get(self, key, default=None):
            return default

    with patch.object(enrichment, "get_config_manager", return_value=_Cfg()), \
         patch.object(enrichment, "strip_all_non_audio_tags"), \
         patch.object(enrichment, "extract_source_metadata", return_value=meta), \
         patch.object(enrichment, "embed_source_ids"), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch.object(enrichment, "embed_album_art_metadata", return_value=False):
        enrichment.enhance_file_metadata(
            path, context=_context(artist), artist={"name": artist}, album_info={})
    return FLAC(path).get("genre")


def test_routed_album_is_tagged_as_audiobook(tmp_path, db, monkeypatch):
    assert _tag_flac(tmp_path, monkeypatch, db, "Bobby & Max", routed=True) == ["Audiobook"]


def test_unrouted_album_keeps_its_source_genre(tmp_path, db, monkeypatch):
    assert _tag_flac(tmp_path, monkeypatch, db, "Coldplay", routed=False) == ["Rock"]


# ---------------------------------------------------------------------------
# review: sync must not see routed ids, folder must exist, writes are admin-only
# ---------------------------------------------------------------------------

def test_a_sync_style_lookup_never_sees_a_routed_id(db, folders):
    """Sync callers ask "is it on the media server"; a ``routed:N`` id means nothing there."""
    _, books = folders
    routing.add_route("Bobby & Max", db)
    episode = books / "Bobby & Max" / "Folge 84" / "01 - Fibis Ferienresort.mp3"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"x")
    _record_download(db, episode, "Fibis Ferienresort", "Bobby & Max")

    for kwargs in ({}, {"server_source": "plex"}, {"album": "Folge 84"}):
        track, _ = db.check_track_exists("Fibis Ferienresort", "Bobby & Max", **kwargs)
        assert track is None
    owned, _ = db.check_track_exists("Fibis Ferienresort", "Bobby & Max", include_downloads=True)
    assert str(owned.id).startswith("routed:")


def test_nothing_is_routed_until_an_audiobook_folder_is_configured(db, folders, monkeypatch):
    routing.add_route("Bobby & Max", db)
    assert routing.routed_root_for_context(_context("Bobby & Max"), db)
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: False)
    assert routing.routed_root_for_context(_context("Bobby & Max"), db) is None


def test_folder_setting_counts_only_when_filled_in(monkeypatch):
    from core.settings import config_manager

    values = {}
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: values.get(key, default))
    assert routing.audiobook_folder_configured() is False
    values["audiobooks.download_path"] = "  "
    assert routing.audiobook_folder_configured() is False
    values["library.audiobooks_path"] = "/media/audiobooks"
    assert routing.audiobook_folder_configured() is True


def _client(db, monkeypatch, *, admin=True, configured=True):
    from flask import Flask

    from api.audiobook_routes import create_blueprint

    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    monkeypatch.setattr(routing, "audiobook_folder_configured", lambda: configured)
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: admin)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    return app.test_client()


def test_routing_is_refused_without_an_audiobook_folder(db, monkeypatch):
    client = _client(db, monkeypatch, configured=False)
    reply = client.post("/api/audiobook-routes", json={"name": "Bobby & Max"})
    assert reply.status_code == 409 and "audiobook folder" in reply.get_json()["error"]
    assert routing.list_routes(db) == []
    assert client.get("/api/audiobook-routes").get_json()["audiobook_folder_configured"] is False


def test_only_admins_can_change_routes_or_move_folders(db, folders, monkeypatch):
    transfer, _ = folders
    _album(transfer, "Bobby & Max", "Folge 84")
    routing.add_route("Bobby & Max", db)
    client = _client(db, monkeypatch, admin=False)
    for method, url, body in (
        ("post", "/api/audiobook-routes", {"name": "Queen"}),
        ("delete", "/api/audiobook-routes/Bobby & Max", None),
        ("post", "/api/audiobook-routes/Bobby & Max/move", {"apply": True}),
        ("post", "/api/audiobook-routes/Bobby & Max/refresh", {}),
    ):
        assert getattr(client, method)(url, json=body).status_code == 403
    assert [a["label"] for a in routing.list_routes(db)] == ["Bobby & Max"]
    assert (transfer / "Bobby & Max" / "Folge 84").is_dir()
    # reading stays open: every card asks which artists are routed
    assert client.post("/api/audiobook-routes/check", json={"names": ["Bobby & Max"]}).status_code == 200
    assert client.get("/api/audiobook-routes").status_code == 200


BOOKS = [
    {"id": 7, "title": "Folge 93: Dämonen aus der Unterwelt", "author": "Bobby & Max",
     "file_count": 27, "audio_format": "mp3"},
    {"id": 8, "title": "Folge 9: Der Anfang", "author": "Bobby & Max",
     "file_count": 10, "audio_format": "mp3"},
    {"id": 9, "title": "Folge 93: Dämonen aus der Unterwelt", "author": "Someone Else",
     "file_count": 5, "audio_format": "mp3"},
]


def test_library_match_finds_the_routed_artists_book(db):
    routing.add_route("Bobby & Max", db)
    hit = routing.routed_library_match("Folge 93: Dämonen aus der Unterwelt", "Bobby & Max", db, BOOKS)
    assert hit and hit["id"] == 7


def test_library_match_ignores_other_authors_and_unrouted_artists(db):
    assert routing.routed_library_match("Folge 93: Dämonen aus der Unterwelt", "Bobby & Max", db, BOOKS) is None
    routing.add_route("Bobby & Max", db)
    assert routing.routed_library_match("Folge 93: Dämonen aus der Unterwelt", "Bobby & Max", db, BOOKS)["author"] == "Bobby & Max"
    assert routing.routed_library_match("Folge 93: Dämonen aus der Unterwelt", "Nobody", db, BOOKS) is None


def test_library_match_keeps_episode_numbers_apart(db):
    routing.add_route("Bobby & Max", db)
    assert routing.routed_library_match("Folge 90: Der Anfang", "Bobby & Max", db, BOOKS) is None
    assert routing.routed_library_match("Folge 9: Der Anfang!", "Bobby & Max", db, BOOKS)["id"] == 8


def test_discography_counts_a_routed_album_as_owned(db, monkeypatch):
    routing.add_route("Bobby & Max", db)
    monkeypatch.setattr("core.audiobook_database.get_audiobook_db",
                        lambda: type("D", (), {"get_library": lambda self: BOOKS})())
    album, conf, owned, expected, complete, formats = db.check_album_exists_with_completeness(
        "Folge 93: Dämonen aus der Unterwelt", "Bobby & Max", expected_track_count=27)
    assert album is not None and conf == 1.0
    assert (owned, expected, complete) == (27, 27, True)
    assert formats == ["MP3"]
    # an album the library does not hold stays missing
    none = db.check_album_exists_with_completeness("Folge 95: Gibt es nicht", "Bobby & Max")
    assert none[0] is None
