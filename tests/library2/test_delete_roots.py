"""Which folders ADR-05 accepts as "inside the library".

Reported: "wenn ich ein file löschen möchte steht immer noch Permanent deletion
is blocked for 1 unsafe or unresolved file" — on a library where every file was
imported by SoulSync itself.

The containment rule only ever accepted ``library.music_paths``. That setting
is optional, defaults to ``[]``, and nothing forces a user to fill it: in the
reporter's config it is literally ``null`` while every catalogue path sits
under the organize destination (``soulseek.transfer_path``), which is where
SoulSync's own import pipeline files the library. With no configured root,
``_containing_root`` matches nothing, every file previews as
``outside_configured_library_roots``, and permanent deletion is impossible
forever — with an error that never says which setting to fill.

The folder SoulSync organizes INTO is a library root by construction. Adding it
keeps the boundary bounded (a path on some unrelated mount is still refused)
while making the delete work on a default install.

What does NOT change is :func:`fuzzy_resolved_path_is_deletable`, whose whole
purpose (iss29-E04) is to refuse a resolver GUESS that landed in the transfer
folder — see test_fuzzy_delete_containment.py. A guess is not a user pointing
at a specific album's files.
"""

from __future__ import annotations

from core.library2.file_delete import (
    fuzzy_resolved_path_is_deletable,
    preview_entity_files,
)


class _Config:
    """Config with the paths a real install has, and no music_paths at all."""

    def __init__(self, *, music_paths=None, transfer=None, download=None):
        self.values = {
            "library.music_paths": music_paths if music_paths is not None else [],
            "soulseek.transfer_path": transfer or "",
            "soulseek.download_path": download or "",
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


def _album_file(conn, path) -> int:
    track_id, album_id = conn.execute(
        "SELECT id, album_id FROM lib2_tracks ORDER BY id LIMIT 1"
    ).fetchone()
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track_id,))
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
        (track_id, str(path)),
    )
    conn.commit()
    return int(album_id)


def test_the_organize_destination_is_a_library_root(imported_conn, legacy_db, tmp_path):
    """The reported case: no music_paths configured, files under the folder
    SoulSync imports into."""
    transfer = tmp_path / "Transfer"
    target = transfer / "Michael Jackson" / "Thriller" / "01 - Beat It.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"audio")
    album_id = _album_file(imported_conn, target)

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(transfer)),
    )

    assert preview["unsafe_count"] == 0, preview["files"]
    assert preview["deletable_count"] == 1
    assert preview["files"][0]["root"] == str(transfer.resolve())


def test_a_configured_music_path_still_wins_where_it_applies(
        imported_conn, legacy_db, tmp_path):
    """Adding the import root must not shrink what was already accepted."""
    music = tmp_path / "music"
    target = music / "Michael Jackson" / "Thriller" / "01 - Beat It.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"audio")
    album_id = _album_file(imported_conn, target)

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(music_paths=[str(music)], transfer=str(tmp_path / "Transfer")),
    )

    assert preview["unsafe_count"] == 0
    assert preview["files"][0]["root"] == str(music.resolve())


def test_a_path_on_an_unrelated_mount_is_still_refused(
        imported_conn, legacy_db, tmp_path):
    """The boundary stays a boundary — this is not "delete anything"."""
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    stray = tmp_path / "somewhere-else" / "01 - Beat It.flac"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"audio")
    album_id = _album_file(imported_conn, stray)

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(transfer)),
    )

    assert preview["unsafe_count"] == 1
    assert preview["files"][0]["reason"] == "outside_configured_library_roots"


def test_the_incoming_download_folder_is_not_a_library_root(
        imported_conn, legacy_db, tmp_path):
    """Downloads in flight are not library files. The import pipeline moves
    them into the organize destination; until then they belong to the
    downloader, and the library's delete command has no business there."""
    downloads = tmp_path / "downloads"
    incoming = downloads / "incomplete" / "01 - Beat It.flac"
    incoming.parent.mkdir(parents=True)
    incoming.write_bytes(b"audio")
    album_id = _album_file(imported_conn, incoming)

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer"),
                               download=str(downloads)),
    )

    assert preview["unsafe_count"] == 1


def test_a_resolver_guess_in_the_transfer_folder_is_still_refused(tmp_path):
    """iss29-E04 stands: widening the DELETE boundary must not widen the rule
    that validates a GUESS. A fresh import sits in the same layout, and a
    guess landing on it would destroy a download the user just made."""
    transfer = tmp_path / "Transfer"
    guessed = transfer / "Artist" / "Album" / "01 Song.flac"
    guessed.parent.mkdir(parents=True)
    guessed.write_bytes(b"audio")
    recorded = str(tmp_path / "library" / "Artist" / "Album" / "01 Song.flac")

    assert fuzzy_resolved_path_is_deletable(
        recorded, str(guessed), _Config(transfer=str(transfer)),
    ) is False


def test_missing_declared_root_does_not_authorize_deleting_another_copy(
        imported_conn, legacy_db, tmp_path):
    from core.library2.file_delete import delete_entity_files

    original_root = tmp_path / "original"
    original_root.mkdir()
    (original_root / ".mounted").write_bytes(b"mounted")
    missing = original_root / "Artist/Album/song.flac"
    other_root = tmp_path / "independent"
    other = other_root / "Artist/Album/song.flac"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"independent copy")
    album_id = _album_file(imported_conn, missing)
    config = _Config(music_paths=[str(original_root), str(other_root)])

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id, config_manager=config,
    )
    # Confirm the real user action, then assert on its physical side effect.
    delete_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        preview_token=preview["preview_token"], config_manager=config,
    )

    assert other.exists(), "delete must not unlink a different declared root's copy"
    assert other.read_bytes() == b"independent copy"
    assert preview["deletable_count"] == 0


def test_destructive_guard_refuses_cross_declared_root_guesses(tmp_path):
    original = tmp_path / "unmounted"
    independent = tmp_path / "independent"
    target = independent / "Artist/Album/song.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"independent")

    assert fuzzy_resolved_path_is_deletable(
        str(original / "Artist/Album/song.flac"), str(target),
        _Config(music_paths=[str(original), str(independent)]),
    ) is False


def test_destructive_guard_retains_a_missing_windows_roots_identity(tmp_path, monkeypatch):
    import os

    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda path: False if path == "/.dockerenv" else real_exists(path))
    root = tmp_path / "local"
    target = root / "Artist/Album/song.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"independent")

    assert fuzzy_resolved_path_is_deletable(
        r"H:\Music\Artist\Album\song.flac", str(target),
        _Config(music_paths=[r"H:\Music", str(root)]),
    ) is False


# --- the media server's own folders and each profile's own library ----------

class _Server:
    def __init__(self, locations):
        self.locations = locations
        self.calls = 0

    def get_music_library_locations(self):
        self.calls += 1
        return list(self.locations)


class _Engine:
    def __init__(self, client, server="plex"):
        self._client = client
        self.active_server = server

    def active_client(self):
        return self._client


def _with_server(monkeypatch, locations, server="plex"):
    from core.library2 import file_delete
    from core.media_server import engine as engine_module

    client = _Server(locations)
    monkeypatch.setattr(file_delete, "MEDIA_SERVER_FOLDERS", True)
    monkeypatch.setattr(engine_module, "_default_engine", _Engine(client, server))
    return client


def _song(root):
    target = root / "Michael Jackson" / "Thriller" / "01 - Beat It.flac"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"audio")
    return target


def test_the_media_servers_music_folder_is_a_library_root(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """an upgraded plex install: music_paths was never filled, the files sit
    in the folder plex scans, and 3.6.1 deleted them. they still delete."""
    music = tmp_path / "PlexMusic"
    album_id = _album_file(imported_conn, _song(music))
    _with_server(monkeypatch, [str(music)])

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer")),
    )

    assert preview["unsafe_count"] == 0, preview["files"]
    assert preview["files"][0]["root"] == str(music.resolve())


def test_a_server_folder_maps_through_a_learned_mount(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """jellyfin calls it /srv/media/music, soulsync mounts it elsewhere; the
    mount the scan learned maps the folder."""
    from core.library import server_paths

    local = tmp_path / "mnt" / "music"
    album_id = _album_file(imported_conn, _song(local))
    server_paths.reset()
    monkeypatch.setitem(server_paths._learned, "/srv/media", str(tmp_path / "mnt"))
    _with_server(monkeypatch, ["/srv/media/music"], server="jellyfin")

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer")),
    )

    assert preview["unsafe_count"] == 0, preview["files"]
    assert preview["files"][0]["root"] == str(local.resolve())


def test_a_server_folder_holding_the_downloads_is_not_a_root(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """a server that scans /data, with downloads in /data/downloads, does not
    open the download folder to the library's delete."""
    data = tmp_path / "data"
    downloads = data / "downloads"
    album_id = _album_file(imported_conn, _song(downloads))
    _with_server(monkeypatch, [str(data)])

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer"), download=str(downloads)),
    )

    assert preview["unsafe_count"] == 1


def test_a_server_folder_soulsync_cant_see_adds_nothing(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """a remote server's folder that maps to nothing here is no root, and the
    stray file stays refused."""
    stray = _song(tmp_path / "somewhere-else")
    album_id = _album_file(imported_conn, stray)
    _with_server(monkeypatch, ["/remote/only/music", "/"])

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer")),
    )

    assert preview["unsafe_count"] == 1
    assert preview["files"][0]["reason"] == "outside_configured_library_roots"


def test_the_server_is_asked_once_per_ten_minutes(tmp_path, monkeypatch):
    from core.library2.file_delete import _library_roots

    music = tmp_path / "PlexMusic"
    music.mkdir()
    client = _with_server(monkeypatch, [str(music)])

    for _ in range(3):
        assert str(music.resolve()) in _library_roots(_Config(transfer=str(tmp_path / "T")))
    assert client.calls == 1


def test_a_profiles_own_library_folder_is_a_root(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """a profile that keeps its own library imports into its own folder; its
    files delete the way the shared library's do."""
    from core.library2 import library_roots

    own = tmp_path / "Kids"
    album_id = _album_file(imported_conn, _song(own))
    monkeypatch.setattr(library_roots, "configured_roots",
                        lambda database=None: {str(tmp_path / "Transfer") + "/": 0,
                                               str(own) + "/": 2})

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id,
        config_manager=_Config(transfer=str(tmp_path / "Transfer")),
    )

    assert preview["unsafe_count"] == 0, preview["files"]
    assert preview["files"][0]["root"] == str(own.resolve())


def test_a_resolver_guess_in_an_own_library_folder_is_refused(tmp_path, monkeypatch):
    """own folders are import roots too, so a guess landing there is the
    iss29-E04 hazard and stays refused."""
    from core.library2 import library_roots

    own = tmp_path / "Kids"
    guess = _song(own)
    monkeypatch.setattr(library_roots, "configured_roots",
                        lambda database=None: {str(own) + "/": 2})

    assert not fuzzy_resolved_path_is_deletable(
        str(tmp_path / "gone" / "01 - Beat It.flac"), str(guess),
        config_manager=_Config(),
    )
