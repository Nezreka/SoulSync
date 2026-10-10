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


def test_parse_provider_order():
    po = subtitles.parse_provider_order
    assert po('["opensubtitles", "podnapisi"]') == ["opensubtitles", "podnapisi"]
    assert po(["a", "b"]) == ["a", "b"]                        # list form (UI posts this)
    assert po(("a",)) == ["a"]
    assert po(["ok", 42, "", None]) == ["ok"]                  # non-strings dropped
    assert po(["a", "a", "b"]) == ["a", "b"]                   # de-duped, order kept
    assert po(["  a  "]) == ["a"]                              # stripped
    assert po("garbage {{{") == ["opensubtitles"]             # fallback
    assert po([]) == ["opensubtitles"]                        # empty → default
    assert po(None) == ["opensubtitles"]
    assert po({"not": "a list"}) == ["opensubtitles"]


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


def test_srt_name_hi_forced_tiers():
    # Bazarr/Plex/Jellyfin convention: hi/forced variants get distinct names.
    v = "/lib/M (2020)/M (2020) Bluray-1080p.mkv"
    assert subtitles.srt_name(v, "en") == "M (2020) Bluray-1080p.en.srt"
    assert subtitles.srt_name(v, "en", hi=True) == "M (2020) Bluray-1080p.en.hi.srt"
    assert subtitles.srt_name(v, "en", forced=True) == \
        "M (2020) Bluray-1080p.en.forced.srt"
    assert subtitles.srt_name(v, "en", hi=True, forced=True) == \
        "M (2020) Bluray-1080p.en.hi.forced.srt"


def test_srt_name_hi_and_forced_rows_never_collide():
    # The overwrite bug the Phase 1 gap warned about: the four variants for one
    # video+language must be four distinct filenames.
    v = "/lib/Show/Show - S01E01.mkv"
    names = {subtitles.srt_name(v, "en"),
             subtitles.srt_name(v, "en", hi=True),
             subtitles.srt_name(v, "en", forced=True),
             subtitles.srt_name(v, "en", hi=True, forced=True)}
    assert len(names) == 4


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


def test_parse_langs_drops_path_traversal():
    # N1 (security): a language code flows into the sidecar filename
    # (srt_name) — traversal payloads must never survive parsing.
    assert subtitles.parse_langs("../../evil, en") == ["en"]
    assert subtitles.parse_langs("../../../../../../tmp/pwned, en") == ["en"]
    assert subtitles.parse_langs("en, /abs/path, es") == ["en", "es"]
    assert subtitles.parse_langs("..") == ["en"]          # all dropped → default


def test_valid_lang_code():
    assert subtitles.valid_lang_code("en")
    assert subtitles.valid_lang_code("pt-br")
    assert subtitles.valid_lang_code("zh-tw")
    assert not subtitles.valid_lang_code("../../evil")
    assert not subtitles.valid_lang_code("/abs")
    assert not subtitles.valid_lang_code("e")             # too short
    assert not subtitles.valid_lang_code("toolongcode")
    assert not subtitles.valid_lang_code("")
    assert not subtitles.valid_lang_code(None)


def test_normalize_drops_traversal_from_subtitle_langs():
    # The settings write path: junk can never be STORED via the UI/API.
    from core.video.organization import normalize
    assert normalize({"subtitle_langs": "../../evil, en"})["subtitle_langs"] == "en"
    assert normalize({"subtitle_langs": "en, es"})["subtitle_langs"] == "en,es"


def test_traversal_setting_never_reaches_filesystem(tmp_path):
    # End-to-end: even if a junk setting were somehow stored (legacy), the
    # hook resolves it through parse_langs — no junk row, no escaping file.
    import json as _json
    from database.video_database import VideoDatabase
    from core.video.download_monitor import write_subtitles_for

    db = VideoDatabase(database_path=str(tmp_path / "v.db"))

    class _FS:
        def __init__(self):
            self.texts = {}
        def list_dir(self, folder):
            return []
        def write_text(self, path, content):
            self.texts[path] = content

    dl_id = db.add_video_download({"kind": "movie", "title": "M", "status": "completed",
                                   "media_id": "603", "media_source": "tmdb",
                                   "search_ctx": _json.dumps({})})
    dl = db.get_video_download(dl_id)
    fs = _FS()
    write_subtitles_for(db, dl, "/media/Films/Film.mkv",
                        {"subtitle_langs": "../../../../../../tmp/pwned, en"}, fs)
    rows = db.subtitle_get_for_video("download", dl_id)
    assert [r["language"] for r in rows] == ["en"]
    for path in fs.texts:
        assert os.path.normpath(os.path.dirname(path)) == \
            os.path.normpath("/media/Films"), path
