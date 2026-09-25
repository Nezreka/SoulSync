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
