"""Artwork comes from what the library already has before any provider.

Plex, Jellyfin and Navidrome read a folder's cover.jpg and an artist folder's
artist.jpg first; the upgrade ignored both, and the picture the library
already showed (5,613 artists and 71k albums on a live install carried a
media-server image, ~1,800 artists a provider image an enrichment worker
picked) was looked up again instead, so an artist could change face.
"""

from __future__ import annotations

import io
import sqlite3
from types import SimpleNamespace

import pytest

from core.library2 import artwork
from core.library2.schema import ensure_library_v2_schema


def _jpeg(color=(200, 30, 30)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def lib(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    artist = conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('Muse','muse')").lastrowid
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Origin')",
                         (artist,)).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Plug', 1)",
                         (album,)).lastrowid
    folder = tmp_path / "Music" / "Muse" / "Origin"
    folder.mkdir(parents=True)
    (folder / "01 Plug.flac").write_bytes(b"not audio")
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, file_state) "
                 "VALUES(?, ?, 1, 'active')", (track, str(folder / "01 Plug.flac")))
    monkeypatch.setattr(artwork, "_resolve_abs", lambda path, _cfg: path)
    return SimpleNamespace(conn=conn, artist=artist, album=album, folder=folder, tmp=tmp_path)


def test_an_album_folders_cover_is_found(lib):
    (lib.folder / "Cover.JPG").write_bytes(b"cover-bytes")
    assert artwork._folder_art_for_album(lib.conn, None, lib.album) == b"cover-bytes"


def test_a_disc_folder_reads_the_release_folders_cover(lib):
    disc = lib.folder / "CD1"
    disc.mkdir()
    (disc / "02.flac").write_bytes(b"x")
    lib.conn.execute("UPDATE lib2_track_files SET path=?", (str(disc / "02.flac"),))
    (lib.folder / "folder.jpg").write_bytes(b"release-cover")
    assert artwork._folder_art_for_album(lib.conn, None, lib.album) == b"release-cover"


def test_artist_jpg_in_the_artists_own_folder(lib):
    (lib.folder.parent / "artist.jpg").write_bytes(b"portrait")
    assert artwork._folder_art_for_artist(lib.conn, None, lib.artist) == b"portrait"


def test_a_shared_roots_folder_jpg_is_nobodys_portrait(lib):
    # the release sits straight under the music root: the folder above it is
    # "Music", not the artist, so its folder.jpg is not this artist's photo
    lib.conn.execute("UPDATE lib2_track_files SET path=?", (str(lib.tmp / "Music" / "x.flac"),))
    (lib.tmp / "folder.jpg").write_bytes(b"root-art")
    assert artwork._folder_art_for_artist(lib.conn, None, lib.artist) is None


def test_the_stored_media_server_image_comes_through_the_server(lib, monkeypatch):
    lib.conn.execute("UPDATE lib2_artists SET image_url='/library/metadata/1/thumb/2'")
    client = SimpleNamespace(image_url=lambda thumb: f"http://plex.lan:32400{thumb}?X-Plex-Token=t")
    monkeypatch.setattr("core.media_server.engine.get_media_server_engine",
                        lambda: SimpleNamespace(active_client=lambda: client))
    fetched = []

    class _Response:
        status_code = 200
        raw = SimpleNamespace(read=lambda n, decode_content=True: b"plex-photo")

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: fetched.append(url) or _Response())
    assert artwork._stored_image(lib.conn, "artist", lib.artist) == b"plex-photo"
    assert fetched == ["http://plex.lan:32400/library/metadata/1/thumb/2?X-Plex-Token=t"]


def test_an_artist_keeps_the_photo_it_had_before_any_provider_is_asked(lib, monkeypatch):
    photo = _jpeg()
    monkeypatch.setattr(artwork, "_stored_image", lambda conn, kind, eid: photo)
    monkeypatch.setattr(artwork, "_provider_art_url",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked a provider")))
    database = SimpleNamespace(database_path=str(lib.tmp / "music.db"))
    out = artwork._build_artwork_unlocked(database, lib.conn, None, "artist", lib.artist)
    assert out and open(out, "rb").read()


def test_an_album_reads_its_folder_before_any_provider(lib, monkeypatch):
    (lib.folder / "cover.jpg").write_bytes(_jpeg((10, 200, 10)))
    monkeypatch.setattr("core.metadata.art_apply.extract_embedded_art", lambda path: None)
    monkeypatch.setattr(artwork, "_provider_art_url",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked a provider")))
    database = SimpleNamespace(database_path=str(lib.tmp / "music.db"))
    assert artwork._build_artwork_unlocked(database, lib.conn, None, "album", lib.album)
