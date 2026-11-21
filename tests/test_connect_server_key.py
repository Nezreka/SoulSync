"""the server_key file and its copy in the database (PAIRING_SPEC.md 1, 8:
backup and restore keeps the identity; a fresh install gets a new one)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import sys

import pytest
from cryptography.fernet import Fernet

from core.connect import server_key
from core.connect.identity import KEY_FILE_NAME, load_or_create_identity, read_key_file


class _Meta:
    """the two metadata calls, on a real sqlite file so a backup is a file copy"""

    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")

    def get_metadata(self, key):
        with sqlite3.connect(self.path) as c:
            row = c.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_metadata(self, key, value):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))


class _Crypt:
    """config_manager's two Fernet calls with its own key file"""

    def __init__(self, key: bytes):
        self.f = Fernet(key)

    def _encrypt_value(self, value):
        return self.f.encrypt(str(value).encode()).decode()

    def _decrypt_value(self, value):
        if not isinstance(value, str) or not value.startswith("gAAAAA"):
            return value
        try:
            return self.f.decrypt(value.encode()).decode()
        except Exception:  # noqa: BLE001 - like config_manager: the token comes back
            return value


def _load(directory, db, crypt):
    get, put = server_key.db_mirror(db, crypt)
    return server_key.load_or_create(directory, mirror_get=get, mirror_set=put)


def _chmod_works(directory) -> bool:
    probe = directory / "probe"
    probe.write_text("x")
    os.chmod(probe, 0o600)
    ok = stat.S_IMODE(os.stat(probe).st_mode) == 0o600
    probe.unlink()
    return ok and sys.platform != "win32"


def test_first_start_makes_a_key_file_0600(tmp_path, monkeypatch):
    modes = []
    real_chmod = os.chmod
    monkeypatch.setattr(os, "chmod", lambda p, m: (modes.append(m), real_chmod(p, m)))
    ident = load_or_create_identity(tmp_path)
    file = tmp_path / KEY_FILE_NAME
    assert file.read_text().strip() == ident.key
    assert len(ident.key) == 64
    assert modes == [0o600]
    monkeypatch.setattr(os, "chmod", real_chmod)
    # a drive that keeps modes (not a windows mount) shows it too
    if _chmod_works(tmp_path):
        assert stat.S_IMODE(os.stat(file).st_mode) == 0o600
    # and the same one every start after
    assert load_or_create_identity(tmp_path) == ident


def test_a_damaged_key_file_is_never_replaced(tmp_path):
    (tmp_path / KEY_FILE_NAME).write_text("not hex\n")
    with pytest.raises(ValueError):
        load_or_create_identity(tmp_path)
    with pytest.raises(ValueError):
        _load(tmp_path, _Meta(tmp_path / "db.sqlite"), _Crypt(Fernet.generate_key()))
    assert (tmp_path / KEY_FILE_NAME).read_text() == "not hex\n"


def test_the_database_copy_is_encrypted(tmp_path):
    db = _Meta(tmp_path / "db.sqlite")
    ident = _load(tmp_path / "state", db, _Crypt(Fernet.generate_key()))
    raw = db.get_metadata(server_key.METADATA_KEY)
    assert raw.startswith("gAAAAA")
    assert ident.key not in raw
    with open(db.path, "rb") as fh:
        assert ident.key.encode() not in fh.read()


def test_backup_and_restore_keeps_the_identity(tmp_path):
    fernet_key = Fernet.generate_key()
    live = tmp_path / "live"
    live.mkdir()
    db = _Meta(live / "music_library.db")
    original = _load(live, db, _Crypt(fernet_key))
    # a backup is a copy of the database file
    backup = tmp_path / "music_library.db.backup"
    shutil.copy(db.path, backup)
    # the new machine: the backup and the .encryption_key, no server_key file
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.copy(backup, moved / "music_library.db")
    restored = _load(moved, _Meta(moved / "music_library.db"), _Crypt(fernet_key))
    assert restored == original
    assert read_key_file(moved) == original.key


def test_a_fresh_install_gets_a_new_identity(tmp_path):
    a = _load(tmp_path / "a", _Meta(tmp_path / "a.db"), _Crypt(Fernet.generate_key()))
    b = _load(tmp_path / "b", _Meta(tmp_path / "b.db"), _Crypt(Fernet.generate_key()))
    assert a.lookup_id != b.lookup_id


def test_a_backup_without_its_encryption_key_starts_a_new_identity(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    db = _Meta(live / "music_library.db")
    original = _load(live, db, _Crypt(Fernet.generate_key()))
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.copy(db.path, moved / "music_library.db")
    other = _load(moved, _Meta(moved / "music_library.db"), _Crypt(Fernet.generate_key()))
    assert other != original


def test_the_file_wins_over_an_older_copy(tmp_path):
    crypt = _Crypt(Fernet.generate_key())
    db = _Meta(tmp_path / "db.sqlite")
    first = _load(tmp_path / "state", db, crypt)
    # someone restores a database from another server onto this one
    db.set_metadata(server_key.METADATA_KEY, crypt._encrypt_value("ab" * 32))
    again = _load(tmp_path / "state", db, crypt)
    assert again == first
    assert crypt._decrypt_value(db.get_metadata(server_key.METADATA_KEY)) == first.key


def test_public_view_has_no_secret(tmp_path):
    ident = load_or_create_identity(tmp_path)
    view = server_key.public_view(ident)
    assert set(view) == {"lookup_id", "fingerprint", "public_key"}
    assert ident.key not in str(view)


def test_state_dir_is_the_database_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "database" / "music_library.db"))
    assert server_key.state_dir() == (tmp_path / "database").resolve()
