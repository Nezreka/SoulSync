"""Sample Studio Phase 6 — sample output folders.

- settings defaults (docker vs native) + fallback for pre-existing configs
- folder validation: configured ok, unlisted/traversal refused
- sample_path template rendering (vars, collapsing, sanitizing)
- unique_chop_path disambiguation
- metadata tagging: WAV INFO + FLAC vorbis/picture round-trips, cover-art
  extraction from the source file
- e2e through the real api/sample.py blueprint: save to default folder,
  save to second folder, bad folder rejected, tags readable from disk,
  removing a folder keeps existing chops playable, /sample/folders,
  folder-column migration.
"""

from tests.lib2_seed import file_track
import os
import struct

import numpy as np
import pytest
import soundfile as sf
from flask import Blueprint, Flask

from core.sample import folders as sample_folders
from core.sample import organize as sample_organize
from core.sample import tags as sample_tags

SR = 22050


def _tone(path, seconds=2.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    y = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(path), y, sr, subtype="PCM_16")


def _flac_tone(path, seconds=2.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    y = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(path), y, sr, format="FLAC")


def _read_wav_info(path):
    """Minimal LIST INFO reader for tests (independent of tags.py)."""
    with open(path, "rb") as f:
        data = f.read()
    out = {}
    i = 12
    while i + 8 <= len(data):
        fourcc, size = data[i : i + 4], struct.unpack("<I", data[i + 4 : i + 8])[0]
        body = data[i + 8 : i + 8 + size]
        if fourcc == b"LIST" and body[:4] == b"INFO":
            j = 4
            while j + 8 <= len(body):
                k = body[j : j + 4].decode("ascii", "replace")
                sz = struct.unpack("<I", body[j + 4 : j + 8])[0]
                out[k] = body[j + 8 : j + 8 + sz].split(b"\x00")[0].decode("utf-8")
                j += 8 + sz + (sz % 2)
        i += 8 + size + (size % 2)
    return out


# ── settings defaults ────────────────────────────────────────────────


def test_default_sample_paths_native(monkeypatch):
    import core.settings as settings_mod

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.delenv("SOULSYNC_IN_DOCKER", raising=False)
    assert settings_mod.default_sample_paths() == ["./samples"]


def test_default_sample_paths_docker(monkeypatch):
    import core.settings as settings_mod

    monkeypatch.setattr(os.path, "exists", lambda p: p == "/.dockerenv")
    monkeypatch.delenv("SOULSYNC_IN_DOCKER", raising=False)
    assert settings_mod.default_sample_paths() == ["/app/samples"]


def test_get_sample_folders_falls_back_when_unset(monkeypatch):
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [])
    folders = sample_folders.get_sample_folders()
    assert folders == ["./samples"]  # native default in this env
    assert sample_folders.default_sample_folder() == folders[0]


def test_get_sample_folders_uses_configured(monkeypatch, tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [a, b])
    assert sample_folders.get_sample_folders() == [a, b]
    assert sample_folders.default_sample_folder() == a


def test_clean_paths_strips_blanks_and_dedupes():
    assert sample_folders._clean_paths(["a", "b", "a", " ", None, ""]) == ["a", "b"]
    assert sample_folders._clean_paths("not-a-list") == []
    assert sample_folders._clean_paths(None) == []


# ── folder validation ────────────────────────────────────────────────


def test_resolve_sample_folder_default_creates_dir(tmp_path, monkeypatch):
    dest = tmp_path / "s1"
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [str(dest)])
    resolved = sample_folders.resolve_sample_folder(None)
    assert resolved == os.path.abspath(str(dest))
    assert os.path.isdir(resolved)


def test_resolve_sample_folder_accepts_second(tmp_path, monkeypatch):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [a, b])
    assert sample_folders.resolve_sample_folder(b) == os.path.abspath(b)


def test_resolve_sample_folder_rejects_unlisted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sample_folders, "_configured_paths", lambda: [str(tmp_path / "a")]
    )
    with pytest.raises(ValueError):
        sample_folders.resolve_sample_folder("/etc")


def test_resolve_sample_folder_rejects_traversal(tmp_path, monkeypatch):
    base = str(tmp_path / "samples")
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [base])
    with pytest.raises(ValueError):
        sample_folders.resolve_sample_folder(base + "/../evil")
    with pytest.raises(ValueError):
        sample_folders.resolve_sample_folder(base + "/..")


# ── template rendering ───────────────────────────────────────────────


def test_render_sample_path_all_vars():
    segs = sample_organize.render_sample_path(
        None,
        {"artist": "Artist", "track": "Track", "album": "Album",
         "chop": "my chop", "stem": "drums"},
    )
    assert segs == ["Artist", "Track - my chop"]


def test_render_sample_path_collapses_empty_segments():
    segs = sample_organize.render_sample_path(
        "$artist/$album - $chop",
        {"artist": "", "track": "T", "album": "", "chop": "c", "stem": ""},
    )
    assert segs == ["c"]


def test_render_sample_path_sanitizes():
    segs = sample_organize.render_sample_path(
        "$artist/$chop",
        {"artist": 'a:b/c?d', "track": "", "album": "", "chop": "x", "stem": ""},
    )
    assert segs[0] == "a_b_c_d"


def test_render_sample_path_braced_vars_and_stem():
    segs = sample_organize.render_sample_path(
        "${artist}/${track} [${stem}] - ${chop}",
        {"artist": "A", "track": "T", "album": "", "chop": "c", "stem": "vocals"},
    )
    assert segs == ["A", "T [vocals] - c"]


def test_render_sample_path_falls_back_to_chop_name():
    segs = sample_organize.render_sample_path("$artist", {"artist": "", "chop": "c"})
    assert segs == ["c"]


def test_unique_chop_path_disambiguates(tmp_path):
    p1 = sample_organize.unique_chop_path(str(tmp_path), ["A", "T - c"], ".wav")
    assert p1.endswith(os.path.join("A", "T - c.wav"))
    open(p1, "w").close()
    p2 = sample_organize.unique_chop_path(str(tmp_path), ["A", "T - c"], ".wav")
    assert p2.endswith(os.path.join("A", "T - c (2).wav"))


# ── tagging ──────────────────────────────────────────────────────────


def test_tag_wav_roundtrip(tmp_path):
    wav = tmp_path / "chop.wav"
    _tone(wav)
    sample_tags.tag_wav(
        str(wav),
        {"title": "my chop", "artist": "Some Artist",
         "album": "Sample Studio", "comment": "cömment ✓"},
    )
    info = _read_wav_info(str(wav))
    assert info["INAM"] == "my chop"
    assert info["IART"] == "Some Artist"
    assert info["IPRD"] == "Sample Studio"
    assert info["ICMT"] == "cömment ✓"
    # Audio still intact.
    y, sr = sf.read(str(wav))
    assert sr == SR and len(y) == 2 * SR


def test_tag_flac_roundtrip_with_art(tmp_path):
    flac = tmp_path / "chop.flac"
    _flac_tone(flac)
    art = (b"\xff\xd8\xff\x00fakejpeg", "image/jpeg")
    sample_tags.tag_chop_file(
        str(flac), "flac",
        {"title": "my chop", "artist": "A", "album": "Sample Studio",
         "comment": "hello"},
        art=art,
    )
    from mutagen.flac import FLAC

    f = FLAC(str(flac))
    assert f["title"][0] == "my chop"
    assert f["artist"][0] == "A"
    assert f["comment"][0] == "hello"
    assert len(f.pictures) == 1
    assert bytes(f.pictures[0].data) == art[0]


def test_extract_cover_art_from_source_flac(tmp_path):
    from mutagen.flac import FLAC, Picture

    src = tmp_path / "src.flac"
    _flac_tone(src)
    pic = Picture()
    pic.type = 3
    pic.mime = "image/png"
    pic.data = b"\x89PNG\r\n\x1a\nfakepng"
    f = FLAC(str(src))
    f.add_picture(pic)
    f.save()

    art = sample_tags.extract_cover_art(str(src))
    assert art is not None
    assert art[0] == b"\x89PNG\r\n\x1a\nfakepng"
    assert art[1] == "image/png"


def test_extract_cover_art_none_when_missing(tmp_path):
    wav = tmp_path / "plain.wav"
    _tone(wav)
    assert sample_tags.extract_cover_art(str(wav)) is None


def test_extract_cover_art_sidecar_fallback(tmp_path):
    """No embedded art → the album folder's cover.jpg sidecar (importer output)."""
    d = tmp_path / "album"
    d.mkdir()
    wav = d / "track.wav"
    _tone(wav)
    (d / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"fakecover")
    art = sample_tags.extract_cover_art(str(wav))
    assert art is not None
    assert art[0] == b"\xff\xd8\xff\xe0" + b"fakecover"
    assert art[1] == "image/jpeg"


def test_extract_cover_art_prefers_embedded_over_sidecar(tmp_path):
    from mutagen.flac import FLAC, Picture

    d = tmp_path / "album2"
    d.mkdir()
    flac = d / "track.flac"
    _flac_tone(flac)
    pic = Picture()
    pic.type = 3
    pic.mime = "image/png"
    pic.data = b"\x89PNG\r\n\x1a\nembedded"
    f = FLAC(str(flac))
    f.add_picture(pic)
    f.save()
    (d / "cover.jpg").write_bytes(b"\xff\xd8\xffsidecar")

    art = sample_tags.extract_cover_art(str(flac))
    assert art is not None
    assert art[0] == b"\x89PNG\r\n\x1a\nembedded"
    assert art[1] == "image/png"


def test_build_comment_bits():
    c = sample_tags.build_comment("T", "A", "Al", pitch_st=2,
                                   target_bpm=140, source_bpm=100, stem="drums")
    assert "Chopped from 'T' by A (Al)" in c
    assert "+2 st" in c
    assert "100→140 BPM" in c
    assert "drums stem" in c


# ── e2e through the real blueprint ───────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music.db"))
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})

    import api.sample as sample_api

    monkeypatch.setattr(sample_api, "require_api_key", lambda f: f)

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("sample_folders", __name__)
    sample_api.register_routes(bp)
    app.register_blueprint(bp, url_prefix="/api/v1")

    wav = tmp_path / "track.wav"
    _tone(wav, seconds=4.0)

    db = mdb.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Folder Artist')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Folder Album')")
        file_track(conn, 1, 1, 'Folder Track', str(wav),
        )
        conn.commit()
    finally:
        conn.close()

    with app.test_client() as c:
        yield c


def _save(c, **kw):
    body = {
        "track_id": 1, "start_s": 0, "end_s": 1,
        "name": "folder chop", "format": "wav16",
    }
    body.update(kw)
    return c.post("/api/v1/sample/chop", json=body)


def test_save_chop_lands_in_default_folder(client, sample_tmp_folder):
    c = client
    r = _save(c)
    assert r.status_code == 201, r.get_json()
    entry = r.get_json()["data"]
    expected_dir = os.path.join(
        os.path.abspath(str(sample_tmp_folder)), "Folder Artist")
    assert entry["file_path"].startswith(expected_dir + os.sep)
    assert entry["file_path"].endswith("Folder Track - folder chop.wav")
    assert os.path.isfile(entry["file_path"])
    assert entry["folder"] == os.path.abspath(str(sample_tmp_folder))


def test_save_chop_to_second_folder(client, tmp_path, monkeypatch):
    a, b = str(tmp_path / "sA"), str(tmp_path / "sB")
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [a, b])
    r = _save(client, folder=b)
    assert r.status_code == 201, r.get_json()
    entry = r.get_json()["data"]
    assert entry["file_path"].startswith(os.path.abspath(b) + os.sep)
    assert os.path.isfile(entry["file_path"])


def test_save_chop_rejects_unknown_folder(client):
    r = _save(client, folder="/etc")
    assert r.status_code == 400


def test_save_chop_rejects_traversal_folder(client, sample_tmp_folder):
    evil = os.path.abspath(str(sample_tmp_folder)) + "/../evil"
    r = _save(client, folder=evil)
    assert r.status_code == 400


def test_chop_filename_disambiguates(client, sample_tmp_folder):
    r1 = _save(client, name="same name")
    r2 = _save(client, name="same name")
    assert r1.status_code == 201 and r2.status_code == 201
    p1, p2 = r1.get_json()["data"]["file_path"], r2.get_json()["data"]["file_path"]
    assert p1 != p2
    assert p2.endswith(" (2).wav")
    assert os.path.isfile(p1) and os.path.isfile(p2)


def test_chop_tags_readable_from_disk_wav(client):
    r = _save(client, name="tagged chop", format="wav16")
    assert r.status_code == 201, r.get_json()
    info = _read_wav_info(r.get_json()["data"]["file_path"])
    assert info["INAM"] == "tagged chop"
    assert info["IART"] == "Folder Artist"
    assert info["IPRD"] == "Sample Studio"
    assert "Folder Track" in info["ICMT"]


def test_chop_tags_readable_from_disk_flac(client):
    from mutagen.flac import FLAC

    r = _save(client, name="flac chop", format="flac")
    assert r.status_code == 201, r.get_json()
    f = FLAC(r.get_json()["data"]["file_path"])
    assert f["title"][0] == "flac chop"
    assert f["artist"][0] == "Folder Artist"
    assert f["album"][0] == "Sample Studio"


def test_remove_folder_keeps_chop_playable(client, tmp_path, monkeypatch):
    a, b = str(tmp_path / "keepA"), str(tmp_path / "keepB")
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [a, b])
    r = _save(client, folder=b, name="stays put")
    assert r.status_code == 201, r.get_json()
    entry_id = r.get_json()["data"]["id"]
    file_path = r.get_json()["data"]["file_path"]

    # Folder B leaves the settings — the chop must still play and list.
    monkeypatch.setattr(sample_folders, "_configured_paths", lambda: [a])
    r = client.get(f"/api/v1/sample/stash/{entry_id}/audio")
    assert r.status_code == 200
    r = client.get("/api/v1/sample/stash")
    entries = r.get_json()["data"]["entries"]
    assert any(e["id"] == entry_id for e in entries)
    assert os.path.isfile(file_path)
    # …and delete still removes the file, not just the row.
    r = client.delete(f"/api/v1/sample/stash/{entry_id}")
    assert r.status_code == 200
    assert not os.path.isfile(file_path)


def test_stem_chop_embeds_original_track_art(client, tmp_path, monkeypatch):
    """A stem chop's cover comes from the library track, not the generated
    stem WAV (which has no embedded art)."""
    import database.music_database as mdb
    from core.sample.stems import StubSeparator, separate_track
    from mutagen.flac import FLAC

    db = mdb.get_database()
    conn = db._get_connection()
    try:
        track_path = conn.execute(
            "SELECT path FROM lib2_track_files WHERE track_id = 1").fetchone()[0]
    finally:
        conn.close()

    cover = os.path.join(os.path.dirname(track_path), "cover.jpg")
    with open(cover, "wb") as handle:
        handle.write(b"\xff\xd8\xff\xe0" + b"stemcover")

    separate_track(1, backend=StubSeparator())

    r = client.post("/api/v1/sample/chop", json={
        "track_id": 1, "start_s": 0, "end_s": 1, "name": "stem art chop",
        "format": "flac", "stem": "drums",
    })
    assert r.status_code == 201, r.get_json()
    entry = r.get_json()["data"]
    assert entry["stem"] == "drums"
    f = FLAC(entry["file_path"])
    assert len(f.pictures) == 1
    assert bytes(f.pictures[0].data) == b"\xff\xd8\xff\xe0" + b"stemcover"


def test_folders_endpoint(client, sample_tmp_folder):
    r = client.get("/api/v1/sample/folders")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["folders"] == [os.path.abspath(str(sample_tmp_folder))]
    assert data["default"] == data["folders"][0]


def test_migration_adds_folder_column_to_legacy_stash(tmp_path, monkeypatch):
    """Upgrades from before Phase 6: sample_stash exists without folder."""
    import sqlite3

    db_path = str(tmp_path / "legacy6.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE sample_stash (
               id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
               file_path TEXT NOT NULL)"""
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", db_path)
    import database.music_database as mdb

    monkeypatch.setattr(mdb, "_database_instances", {})
    db = mdb.get_database()
    conn = db._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sample_stash)")}
        assert "folder" in cols
    finally:
        conn.close()
