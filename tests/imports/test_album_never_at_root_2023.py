"""an album folder never lands at the library root (#2023).

SeadogsBooty: after a bulk import, "[2011] Avicii Presents Strictly Miami" and
"[1984] Forever Young" sat at the music root next to "Avicii" and
"Alphaville". two ways got there: a download whose artist context and album
credit named nobody rendered an empty $albumartist, and the existing-album
folder reuse happily reused an album folder that already sat at the root.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import core.imports.paths as import_paths
from core.imports.paths import apply_path_template, build_final_path_for_track
from core.library.existing_album_folder import resolve_existing_album_folder

TEMPLATE = "$albumartist/[$year] $album/$track - $title"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _cfg(monkeypatch, tmp_path, template=TEMPLATE):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": template, "single_path": template, "compilation_path": template,
        },
        "file_organization.collab_artist_mode": "first",
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    return config


def _context(artist_name, album_artists, track_artists, search_artist=""):
    return {
        "artist": {"name": artist_name},
        "album": {"name": "Forever Young", "id": "a1", "release_date": "1984-09-27",
                  "total_tracks": 10, "album_type": "album", "artists": album_artists},
        "track_info": {"name": "Big in Japan", "id": "t1", "track_number": 3,
                       "disc_number": 1, "artists": track_artists},
        "original_search_result": {"title": "Big in Japan", "clean_title": "Big in Japan",
                                   "clean_album": "Forever Young", "artist": search_artist},
        "source": "deezer", "is_album_download": True,
    }


def _album_path(tmp_path, context, artist_name):
    path, _ = build_final_path_for_track(
        context, {"name": artist_name},
        {"is_album": True, "album_name": "Forever Young", "track_number": 3,
         "disc_number": 1, "album_type": "album"},
        ".flac", create_dirs=False)
    return os.path.relpath(path, tmp_path).split(os.sep)


def test_an_album_with_no_named_artist_goes_under_the_tracks_artist(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    parts = _album_path(tmp_path, _context("", [{"name": ""}], [{"name": "Alphaville"}]), "")
    assert parts[:2] == ["Alphaville", "[1984] Forever Young"]


def test_the_search_artist_is_the_last_word(monkeypatch, tmp_path):
    """a soulseek match carries the artist only as the search result's string."""
    _cfg(monkeypatch, tmp_path)
    parts = _album_path(tmp_path, _context("", [], [], search_artist="Alphaville"), "")
    assert parts[:2] == ["Alphaville", "[1984] Forever Young"]


def test_a_single_with_no_named_artist_too(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    context = _context("", [], [{"name": "Alphaville"}])
    path, _ = build_final_path_for_track(context, {"name": ""}, None, ".flac", create_dirs=False)
    assert os.path.relpath(path, tmp_path).split(os.sep)[0] == "Alphaville"


def test_an_empty_album_artist_never_renders_an_empty_folder():
    rendered = apply_path_template(TEMPLATE, {
        "artist": "Alphaville", "albumartist": "", "album": "Forever Young",
        "title": "Big in Japan", "track_number": 3, "year": "1984"})
    assert rendered.startswith("Alphaville/")
    rendered = apply_path_template(TEMPLATE, {
        "artist": "", "albumartist": "", "album": "Forever Young",
        "title": "Big in Japan", "track_number": 3, "year": "1984"})
    assert rendered.startswith("Unknown Artist/")


class _FakeDb:
    def __init__(self, files):
        self._files = files

    def get_album_by_spotify_album_id(self, sid):
        return None

    def check_album_exists_with_editions(self, title, artist, **kw):
        return SimpleNamespace(id=1, title="Forever Young"), 0.95

    def get_tracks_by_album(self, album_id):
        return [SimpleNamespace(file_path=f) for f in self._files]


def _root_album(tmp_path):
    folder = tmp_path / "[1984] Forever Young"
    folder.mkdir()
    track = folder / "01 - A Victory of Love.flac"
    track.write_text("x")
    return folder, str(track)


def test_reuse_skips_an_album_folder_at_the_root(tmp_path):
    folder, track = _root_album(tmp_path)
    db = _FakeDb([track])
    kwargs = dict(db=db, transfer_dir=str(tmp_path), album_name="Forever Young",
                  album_artist="Alphaville")
    # a flat layout keeps today's reuse
    assert resolve_existing_album_folder(**kwargs) == os.path.normpath(str(folder))
    assert resolve_existing_album_folder(**kwargs, min_depth=2) is None


def test_reuse_keeps_an_album_inside_its_artist_folder(tmp_path):
    folder = tmp_path / "Alphaville" / "[1984] Forever Young"
    folder.mkdir(parents=True)
    track = folder / "01.flac"
    track.write_text("x")
    out = resolve_existing_album_folder(
        db=_FakeDb([str(track)]), transfer_dir=str(tmp_path),
        album_name="Forever Young", album_artist="Alphaville", min_depth=2)
    assert out == os.path.normpath(str(folder))


def test_a_new_track_of_a_root_level_album_goes_under_its_artist(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    _folder, track = _root_album(tmp_path)
    import database.music_database as music_database
    monkeypatch.setattr(music_database, "get_database", lambda: _FakeDb([track]))
    parts = _album_path(
        tmp_path, _context("Alphaville", [{"name": "Alphaville"}], [{"name": "Alphaville"}]),
        "Alphaville")
    assert parts[:2] == ["Alphaville", "[1984] Forever Young"]
