"""External subtitle download (OpenSubtitles) — pure parsing + the write loop. The
HTTP fetch and filesystem are injected, so it runs without network or disk.
"""

from __future__ import annotations

import os

from core.video import subtitles


def test_parse_langs():
    assert subtitles.parse_langs("en, es ; fr") == ["en", "es", "fr"]
    assert subtitles.parse_langs("EN en") == ["en"]          # lower + dedupe
    assert subtitles.parse_langs("") == ["en"]               # default


def test_pick_best_file_takes_most_downloaded_for_the_language():
    found = {"data": [
        {"attributes": {"language": "en", "download_count": 10, "files": [{"file_id": 111}]}},
        {"attributes": {"language": "en", "download_count": 99, "files": [{"file_id": 222}]}},
        {"attributes": {"language": "es", "download_count": 500, "files": [{"file_id": 333}]}},
    ]}
    assert subtitles.pick_best_file(found, "en") == 222       # most-downloaded English
    assert subtitles.pick_best_file(found, "de") is None      # none for German


def test_search_params_movie_vs_episode():
    movie = subtitles.search_params({"tmdb_id": 603, "imdb_id": "tt0133093"}, "en")
    assert movie["imdb_id"] == "0133093" and movie["languages"] == "en"   # imdb wins, tt stripped
    ep = subtitles.search_params({"tmdb_id": 1396, "season": 1, "episode": 3}, "en")
    assert ep["parent_tmdb_id"] == 1396 and ep["season_number"] == 1 and ep["episode_number"] == 3
    assert subtitles.search_params({}, "en") is None          # unidentified


def test_srt_name():
    assert subtitles.srt_name("/lib/M (2020)/M (2020) Bluray-1080p.mkv", "en") == \
        "M (2020) Bluray-1080p.en.srt"


# ── the write loop ────────────────────────────────────────────────────────────
class FakeFS:
    def __init__(self, dirs=None):
        self.dirs = {k: list(v) for k, v in (dirs or {}).items()}
        self.texts = []     # (path, content)

    def list_dir(self, path):
        return self.dirs.get(str(path), [])

    def write_text(self, path, content):
        self.texts.append((path, content))


def test_write_subtitles_fetches_each_missing_language():
    fs = FakeFS()
    calls = []

    def fetch(identity, lang):
        calls.append(lang)
        return "1\n00:00 --> 00:01\n[%s]\n" % lang

    subtitles.write_subtitles("/lib/M (2020)/M (2020).mkv", ["en", "es"], {"tmdb_id": 1}, fetch, fs)
    assert calls == ["en", "es"]
    names = [os.path.basename(p) for p, _c in fs.texts]
    assert names == ["M (2020).en.srt", "M (2020).es.srt"]


def test_write_subtitles_skips_languages_already_present():
    fs = FakeFS(dirs={"/lib/M (2020)": ["M (2020).en.srt"]})   # already have English
    pulled = []
    subtitles.write_subtitles("/lib/M (2020)/M (2020).mkv", ["en", "es"], {"tmdb_id": 1},
                              lambda i, l: pulled.append(l) or "x", fs)
    assert pulled == ["es"]                                    # only the missing one fetched


def test_write_subtitles_best_effort_on_fetch_failure():
    fs = FakeFS()

    def boom(identity, lang):
        raise RuntimeError("quota exceeded")

    subtitles.write_subtitles("/lib/M/M.mkv", ["en"], {"tmdb_id": 1}, boom, fs)
    assert fs.texts == []                                      # nothing written, no raise


def test_no_fetcher_without_a_key():
    assert subtitles.opensubtitles_fetcher("") is None
    assert subtitles.opensubtitles_fetcher(None) is None
