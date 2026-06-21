"""Kodi/Jellyfin sidecar writer — NFO building + the artwork-set write plan. Pure
logic (XML + plan) with the filesystem injected, so it runs without disk or network.
"""

from __future__ import annotations

import os

from core.video import sidecars


class FakeFS:
    def __init__(self, dirs=None):
        self.dirs = {k: list(v) for k, v in (dirs or {}).items()}
        self.made = []
        self.saved = []     # (url, dst)  — artwork
        self.texts = []     # (path, content)  — nfo

    def list_dir(self, path):
        return self.dirs.get(str(path), [])

    def makedirs(self, path):
        self.made.append(str(path))

    def save_url(self, url, dst):
        self.saved.append((url, dst))

    def write_text(self, path, content):
        self.texts.append((path, content))


_MOVIE = {
    "title": "The Matrix", "year": "1999", "overview": "A hacker learns the truth.",
    "tagline": "Free your mind.", "runtime_minutes": 136, "studio": "Warner Bros.",
    "release_date": "1999-03-31", "rating": 8.2, "genres": ["Action", "Science Fiction"],
    "tmdb_id": 603, "imdb_id": "tt0133093",
    "poster_url": "http://img/p.jpg", "backdrop_url": "http://img/b.jpg", "logo": "http://img/l.png",
    "cast": [{"name": "Keanu Reeves", "character": "Neo"}, {"name": "Carrie-Anne Moss", "character": "Trinity"}],
}
_SHOW = {
    "title": "Breaking Bad", "year": "2008", "overview": "A teacher cooks.",
    "network": "AMC", "first_air_date": "2008-01-20", "rating": 9.5,
    "genres": ["Drama"], "tmdb_id": 1396, "tvdb_id": 81189, "imdb_id": "tt0903747",
    "poster_url": "http://img/bbp.jpg", "backdrop_url": "http://img/bbb.jpg", "logo": "http://img/bbl.png",
    "_seasons": [{"season_number": 1, "poster_url": "http://img/s1.jpg"},
                 {"season_number": 2, "poster_url": "http://img/s2.jpg"}],
}


# ── NFO building ──────────────────────────────────────────────────────────────
def test_movie_nfo_has_the_key_fields():
    xml = sidecars.nfo_movie(_MOVIE)
    assert xml.startswith("<?xml")
    assert "<movie>" in xml and "</movie>" in xml
    assert "<title>The Matrix</title>" in xml
    assert "<year>1999</year>" in xml
    assert "<plot>A hacker learns the truth.</plot>" in xml
    assert "<genre>Action</genre>" in xml and "<genre>Science Fiction</genre>" in xml
    assert '<uniqueid type="tmdb" default="true">603</uniqueid>' in xml
    assert '<uniqueid type="imdb">tt0133093</uniqueid>' in xml
    assert "<name>Keanu Reeves</name>" in xml and "<role>Neo</role>" in xml


def test_tvshow_nfo_uses_show_fields():
    xml = sidecars.nfo_tvshow(_SHOW)
    assert "<tvshow>" in xml and "<studio>AMC</studio>" in xml      # network → studio
    assert "<premiered>2008-01-20</premiered>" in xml
    assert '<uniqueid type="tmdb" default="true">1396</uniqueid>' in xml


def test_nfo_omits_absent_fields_and_escapes():
    xml = sidecars.nfo_movie({"title": "A & B <x>", "tmdb_id": 1})
    assert "<title>A &amp; B &lt;x&gt;</title>" in xml
    assert "<plot>" not in xml and "<genre>" not in xml            # absent → omitted, not blank


# ── write plan ────────────────────────────────────────────────────────────────
def test_plan_gates_on_settings():
    none = sidecars.plan_sidecars("movie", _MOVIE, {})            # both off
    assert none["nfo"] is None and none["art"] == []
    both = sidecars.plan_sidecars("movie", _MOVIE, {"save_artwork": True, "write_nfo": True})
    assert both["nfo"][0] == "movie.nfo"
    names = [n for _u, n in both["art"]]
    assert names == ["poster.jpg", "fanart.jpg", "clearlogo.png"]


def test_show_plan_includes_season_posters_and_tvshow_nfo():
    plan = sidecars.plan_sidecars("episode", _SHOW, {"save_artwork": True, "write_nfo": True})
    assert plan["nfo"][0] == "tvshow.nfo"
    names = [n for _u, n in plan["art"]]
    assert "season01-poster.jpg" in names and "season02-poster.jpg" in names


# ── write (idempotent, best-effort) ───────────────────────────────────────────
def test_write_emits_files_and_skips_existing():
    fs = FakeFS(dirs={"/lib/movies/The Matrix (1999)": ["poster.jpg"]})  # poster already there
    sidecars.write("/lib/movies/The Matrix (1999)", "movie", _MOVIE,
                   {"save_artwork": True, "write_nfo": True}, fs)
    art = [n for _u, n in fs.saved]
    assert "fanart.jpg" in [os.path.basename(p) for p in art]
    assert "poster.jpg" not in [os.path.basename(p) for p in art]   # skipped (already present)
    assert fs.texts and fs.texts[0][0].endswith("movie.nfo")


def test_write_for_resolves_movie_folder_and_show_root():
    fs = FakeFS()
    sidecars.write_for("/lib/movies/The Matrix (1999)/The Matrix (1999) Bluray-1080p.mkv",
                       "movie", "http://img/p.jpg", _MOVIE, {"save_artwork": True}, fs)
    assert any(d.startswith("/lib/movies/The Matrix (1999)/") for _u, d in fs.saved)

    fs2 = FakeFS()
    sidecars.write_for("/lib/tv/Breaking Bad/Season 01/Breaking Bad - S01E01.mkv",
                       "episode", "http://img/bbp.jpg", _SHOW, {"save_artwork": True}, fs2)
    # show-level art lands in the show ROOT, not the Season folder
    assert all("/Season 01/" not in d for _u, d in fs2.saved)
    assert any(d == os.path.join("/lib/tv", "Breaking Bad", "poster.jpg") for _u, d in fs2.saved)


def test_write_for_poster_only_when_no_detail():
    # detail fetch unavailable → still drops the poster from the download-row url
    fs = FakeFS()
    sidecars.write_for("/lib/movies/X (2020)/X (2020).mkv", "movie",
                       "http://img/p.jpg", None, {"save_artwork": True}, fs)
    assert [os.path.basename(d) for _u, d in fs.saved] == ["poster.jpg"]
