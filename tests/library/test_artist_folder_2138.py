"""#2138: artist.nfo goes in the artist's folder the naming template made,
not one level above the album folder. and poster.jpg / background.jpg go
beside it when the folder has no artwork of its own.
"""
from __future__ import annotations

import os

import pytest

MBID = "b9ca7096-68de-455f-8a36-1f1ebb2abf2a"


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def _tags(monkeypatch):
    import core.library.file_tags as file_tags
    tags = {"available": True, "tags": {"album_artist": "3JS",
                                        "musicbrainz_albumartistid": MBID}}
    monkeypatch.setattr(file_tags, "read_embedded_tags", lambda p: tags)
    return tags


def _track(root, *folders):
    folder = root.joinpath(*folders)
    folder.mkdir(parents=True, exist_ok=True)
    track = folder / "01 - Song.flac"
    track.write_bytes(b"fake-audio")
    return str(track)


def _cfg(root, album_path=None, **extra):
    values = {"library.write_artist_nfo": True, "library.music_paths": [str(root)]}
    if album_path:
        values["file_organization.templates"] = {"album_path": album_path}
    values.update(extra)
    return Config(**values)


def test_nested_template_puts_nfo_in_the_artist_folder(tmp_path):
    """#2138: Artist/Album/Artist - Album (year)/track wrote it into "Album"."""
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS (Nederpop trio)", "Album", "3JS - 7 (2015)")
    cfg = _cfg(root, "$albumartist ($albumartistdisambiguation)/$albumtype/"
                     "$albumartist - $album ($year)/$track - $title")
    ok, detail = ensure_artist_nfo_for_track(track, cfg)
    assert ok, detail
    assert (root / "3JS (Nederpop trio)" / "artist.nfo").exists()
    assert not (root / "3JS (Nederpop trio)" / "Album" / "artist.nfo").exists()


def test_default_template_still_writes_one_up(tmp_path):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS", "3JS - Nu")
    ok, detail = ensure_artist_nfo_for_track(track, _cfg(root))
    assert ok, detail
    assert (root / "3JS" / "artist.nfo").exists()


def test_disc_folder_below_the_album_does_not_move_it(tmp_path):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS", "3JS - Nu", "Disc 2")
    ok, detail = ensure_artist_nfo_for_track(track, _cfg(root))
    assert ok, detail
    assert (root / "3JS" / "artist.nfo").exists()


def test_fixed_folder_above_the_artist(tmp_path):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "Pop", "3JS", "Nu")
    ok, detail = ensure_artist_nfo_for_track(
        track, _cfg(root, "Pop/$albumartist/$album/$track - $title"))
    assert ok, detail
    assert (root / "Pop" / "3JS" / "artist.nfo").exists()
    assert not (root / "Pop" / "artist.nfo").exists()


def test_self_titled_album_is_not_the_artist_folder(tmp_path):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS", "Album", "3JS")
    ok, detail = ensure_artist_nfo_for_track(
        track, _cfg(root, "$albumartist/$albumtype/$album/$track - $title"))
    assert ok, detail
    assert (root / "3JS" / "artist.nfo").exists()
    assert not (root / "3JS" / "Album" / "3JS" / "artist.nfo").exists()


def test_no_artist_folder_means_no_nfo(tmp_path):
    """"$albumartist - $album/track" has no artist folder; never the album or the root."""
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS - Nu")
    ok, _detail = ensure_artist_nfo_for_track(
        track, _cfg(root, "$albumartist - $album/$track - $title",
                    **{"file_organization.templates": {
                        "album_path": "$albumartist - $album/$track - $title",
                        "single_path": "$albumartist - $title/$title"}}))
    assert not ok
    assert not (root / "3JS - Nu" / "artist.nfo").exists()
    assert not (root / "artist.nfo").exists()


def test_compilation_folder_gets_no_nfo(tmp_path, _tags):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    _tags["tags"]["album_artist"] = "Various Artists"
    root = tmp_path / "music"
    track = _track(root, "Compilations", "Top 2000")
    ok, _detail = ensure_artist_nfo_for_track(track, _cfg(root))
    assert not ok
    assert not (root / "Compilations" / "artist.nfo").exists()
    assert not (root / "Compilations" / "Top 2000" / "artist.nfo").exists()


def test_template_levels():
    from core.library.artist_image import template_artist_levels
    assert template_artist_levels({}) == [0]
    assert template_artist_levels({"album_path": "Music/$albumartist/$album/$t",
                                   "single_path": "$artist/$title/$title"}) == [1, 0]
    assert template_artist_levels({"album_path": "$albumartist - $album/$t",
                                   "single_path": "$albumartist - $title"}) == []


# poster.jpg / background.jpg


class _Art:
    """stands in for core.metadata.artist_artwork: no network."""

    def __init__(self, photo=b"photo-bytes", background=b"bg-bytes"):
        self.photo, self.background, self.asked = photo, background, []

    def candidates(self, name, mbid=""):
        self.asked.append((name, mbid))
        return {"photos": [("p", "fanart.tv")] if self.photo else [],
                "backgrounds": [("b", "fanart.tv")] if self.background else []}

    def first_that_fits(self, options, check):
        if not options:
            return None
        return (self.photo if check == "photo" else self.background), options[0][1]

    fits_photo = "photo"
    fits_background = "background"


def test_writes_poster_and_background(tmp_path):
    """#2138: plex reads poster.jpg and background.jpg from the artist folder."""
    from core.library.artist_image import write_artist_artwork
    art = _Art()
    results = write_artist_artwork(str(tmp_path), "3JS", MBID, artwork=art)
    assert results["poster"][0] and results["background"][0]
    assert (tmp_path / "poster.jpg").read_bytes() == b"photo-bytes"
    assert (tmp_path / "background.jpg").read_bytes() == b"bg-bytes"
    assert art.asked == [("3JS", MBID)]
    assert not any(n.endswith(".tmp") for n in os.listdir(tmp_path))


def test_never_replaces_artwork_the_user_put_there(tmp_path):
    from core.library.artist_image import write_artist_artwork
    (tmp_path / "poster.jpg").write_bytes(b"mine")
    (tmp_path / "Fanart.PNG").write_bytes(b"mine too")
    art = _Art()
    assert write_artist_artwork(str(tmp_path), "3JS", MBID, artwork=art) == {}
    assert art.asked == []  # nothing missing, nothing fetched
    assert (tmp_path / "poster.jpg").read_bytes() == b"mine"
    assert not (tmp_path / "background.jpg").exists()


def test_an_artist_jpg_counts_as_the_photo(tmp_path):
    from core.library.artist_image import write_artist_artwork
    (tmp_path / "artist.jpg").write_bytes(b"from the navidrome button")
    results = write_artist_artwork(str(tmp_path), "3JS", MBID, artwork=_Art())
    assert "poster" not in results
    assert not (tmp_path / "poster.jpg").exists()
    assert (tmp_path / "background.jpg").exists()


def test_nothing_found_writes_nothing(tmp_path):
    from core.library.artist_image import write_artist_artwork
    results = write_artist_artwork(str(tmp_path), "3JS", MBID, artwork=_Art(None, None))
    assert results == {"poster": (False, "no photo found"),
                       "background": (False, "no background found")}
    assert os.listdir(tmp_path) == []


def test_ensure_hands_the_artist_folder_to_artwork(tmp_path):
    """#2138: artwork lands in the same folder as the nfo, nfo or not."""
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS (Nederpop trio)", "Album", "3JS - 7 (2015)")
    cfg = _cfg(root, "$albumartist/$albumtype/$albumartist - $album/$track - $title")
    calls = []
    ensure_artist_nfo_for_track(track, cfg, artwork=lambda *a: calls.append(a))
    ensure_artist_nfo_for_track(track, cfg, artwork=lambda *a: calls.append(a))  # nfo exists now
    folder = str(root / "3JS (Nederpop trio)")
    assert calls == [(folder, "3JS", MBID), (folder, "3JS", MBID)]


def test_setting_off_means_no_artwork_either(tmp_path):
    from core.library.artist_nfo import ensure_artist_nfo_for_track
    root = tmp_path / "music"
    track = _track(root, "3JS", "Nu")
    calls = []
    ok, detail = ensure_artist_nfo_for_track(
        track, Config(**{"library.music_paths": [str(root)]}),
        artwork=lambda *a: calls.append(a))
    assert not ok and detail == "setting disabled"
    assert calls == []


def test_schedule_looks_up_each_folder_once(tmp_path, monkeypatch):
    import core.library.artist_image as artist_image
    import core.library.artist_nfo as artist_nfo

    ran = []

    class _Inline:
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(artist_nfo.threading, "Thread", _Inline)
    monkeypatch.setattr(artist_nfo, "_artwork_tried", set())
    monkeypatch.setattr(artist_image, "write_artist_artwork",
                        lambda folder, name, mbid: ran.append(folder) or {})
    assert artist_nfo.schedule_artist_artwork(str(tmp_path), "3JS", MBID)
    assert not artist_nfo.schedule_artist_artwork(str(tmp_path), "3JS", MBID)
    assert ran == [str(tmp_path)]
