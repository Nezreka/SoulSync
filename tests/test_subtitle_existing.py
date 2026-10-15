"""bazarr parity: "have it" detection, forced tracks, have-row re-checks,
subdl/subsource test buttons.

existing-subtitle detection is pure apart from the folder listing, the
sidecar reader and the ffprobe call, all injected or stubbed here. worker and
hook runs use the same stubbed-registry pattern as test_subtitle_worker.py.
"""

from __future__ import annotations

import os
import urllib.error

import pytest

from core.video import organization
from core.video.subtitles import existing as ex
from core.video.subtitles import worker
from core.video.subtitles.providers.base import (
    SubtitleCandidate,
    SubtitleProvider,
    SubtitleQuery,
    flag_filter,
)
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def _no_real_ffprobe(monkeypatch):
    # hermetic: embedded tracks only come from what a test hands the stub
    ex.clear_probe_cache()
    monkeypatch.setattr(ex, "_probe_streams", lambda path: [])


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _srt(lines):
    out = []
    for i, line in enumerate(lines, 1):
        out.append("%d\n00:00:%02d,000 --> 00:00:%02d,900\n%s\n" % (i, i % 60, i % 60, line))
    return "\n".join(out)


ENGLISH = _srt(["I don't know what you want from me, and it is not my fault."] * 40)
SPANISH = _srt(["No sé qué es lo que quieres de mí, pero está bien."] * 40)
SDH = _srt(["[door slams]" if i % 3 == 0 else "What do you want from me? I have no idea."
            for i in range(60)])


# ── sidecar names ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tokens,expected", [
    (["en"], ("en", False, False)),
    (["eng"], ("en", False, False)),
    (["English"], ("en", False, False)),
    (["en", "hi"], ("en", True, False)),
    (["hi"], ("hi", False, False)),                 # lone .hi is hindi
    (["English", "SDH"], ("en", True, False)),
    (["en", "cc"], ("en", True, False)),
    (["en", "forced"], ("en", False, True)),
    (["en", "hi", "forced"], ("en", True, True)),
    (["pt-BR"], ("pt-br", False, False)),
    (["pt_br", "forced"], ("pt-br", False, True)),
    (["2", "eng"], ("en", False, False)),           # track numbers ignored
    (["default", "ger"], ("de", False, False)),
    ([], (None, False, False)),
    (["release-notes"], (None, False, False)),
])
def test_parse_sidecar_tokens(tokens, expected):
    assert ex.parse_sidecar_tokens(tokens) == expected


def test_external_tracks_reads_every_naming_and_ignores_other_files():
    names = ["Movie.mkv", "Movie.en.srt", "Movie.eng.sdh.ass", "Movie.fr.forced.srt",
             "Movie.nfo", "Movie.txt", "poster.jpg", "Other.en.srt"]
    got = {(t.language, t.hi, t.forced) for t in
           ex.external_tracks("/lib/Movie/Movie.mkv", names=names, read_text=lambda p: None)}
    assert got == {("en", False, False), ("en", True, False), ("fr", False, True)}


def test_external_tracks_attributes_to_the_longest_video_stem():
    # Movie.Part2.en.srt belongs to Movie.Part2.mkv, not Movie.mkv
    names = ["Movie.mkv", "Movie.Part2.mkv", "Movie.Part2.en.srt"]
    assert ex.external_tracks("/lib/x/Movie.mkv", names=names, read_text=lambda p: None) == []
    got = ex.external_tracks("/lib/x/Movie.Part2.mkv", names=names, read_text=lambda p: None)
    assert [(t.language, t.source) for t in got] == [("en", "external")]


def test_unlabeled_sidecar_gets_its_language_detected():
    texts = {"Movie.srt": SPANISH}
    got = ex.external_tracks("/lib/m/Movie.mkv", names=["Movie.mkv", "Movie.srt"],
                             read_text=lambda p: texts.get(os.path.basename(p)))
    assert [(t.language, t.hi) for t in got] == [("es", False)]


def test_unlabeled_sidecar_with_unclear_language_counts_for_nothing():
    got = ex.external_tracks("/lib/m/Movie.mkv", names=["Movie.mkv", "Movie.srt"],
                             read_text=lambda p: _srt(["ok", "hm"] * 5))
    assert got == []


def test_sdh_content_marks_a_plainly_named_sidecar_hi():
    got = ex.external_tracks("/lib/m/Movie.mkv", names=["Movie.en.srt"],
                             read_text=lambda p: SDH)
    assert [(t.language, t.hi) for t in got] == [("en", True)]


def test_detect_language_scripts_and_margins():
    assert ex.detect_language(ENGLISH) == "en"
    assert ex.detect_language(SPANISH) == "es"
    assert ex.detect_language(_srt(["Привет, как дела? Я не знаю."] * 20)) == "ru"
    assert ex.detect_language(_srt(["こんにちは、元気ですか"] * 20)) == "ja"
    assert ex.detect_language("") is None


def test_ass_dialogue_is_read_not_its_headers():
    ass = "[Script Info]\nTitle: x\n[Events]\n" + "\n".join(
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,No sé qué es lo que quieres de mí, pero está bien."
        for _ in range(40))
    assert ex.detect_language(ass) == "es"


# ── embedded tracks ──────────────────────────────────────────────────────────

def _stream(codec="subrip", lang="eng", forced=0, hi=0, title=""):
    return {"codec_name": codec, "tags": {"language": lang, "title": title},
            "disposition": {"forced": forced, "hearing_impaired": hi}}


@pytest.fixture()
def video(tmp_path):
    p = tmp_path / "Movie.mkv"
    p.write_bytes(b"x" * 1024)
    return str(p)


def test_embedded_tracks_flags_codecs_and_switches(video):
    streams = [_stream("hdmv_pgs_subtitle", "eng"),
               _stream("subrip", "ger", title="German Forced"),
               _stream("ass", "spa", hi=1),
               _stream("dvd_subtitle", "fre"),
               _stream("subrip", "und")]
    probe = lambda p: streams  # noqa: E731
    got = {(t.language, t.hi, t.forced) for t in ex.embedded_tracks(video, {}, probe=probe)}
    assert got == {("en", False, False), ("de", False, True), ("es", True, False),
                   ("fr", False, False)}
    ignored = ex.embedded_tracks(video, {"subtitle_ignore_pgs": True,
                                         "subtitle_ignore_vobsub": True,
                                         "subtitle_ignore_ass": True}, probe=probe)
    assert {t.language for t in ignored} == {"de"}
    assert ex.embedded_tracks(video, {"subtitle_use_embedded": False}, probe=probe) == []


def test_embedded_tracks_accept_a_get_setting_callable(video):
    got = ex.embedded_tracks(video, lambda k, d=None: {"subtitle_use_embedded": False}.get(k, d),
                             probe=lambda p: [_stream()])
    assert got == []


# ── coverage rules ───────────────────────────────────────────────────────────

def _t(lang, hi=False, forced=False):
    return ex.Track(lang, hi, forced, "external")


def test_covered_follows_bazarr_rules():
    assert ex.covered([_t("en")], "en")
    assert ex.covered([_t("en", hi=True)], "en")                 # hi covers plain
    assert not ex.covered([_t("en")], "en", hi=True)             # plain doesn't cover hi
    assert not ex.covered([_t("en", forced=True)], "en")         # forced never covers full
    assert not ex.covered([_t("en")], "en", forced=True)         # full doesn't cover forced
    assert ex.covered([_t("en", forced=True)], "en", forced=True)
    assert ex.covered([_t("en-gb")], "en")                       # regional covers bare
    assert not ex.covered([_t("pt")], "pt-br")                   # never the reverse
    assert not ex.covered([_t("es")], "en")


# ── settings ─────────────────────────────────────────────────────────────────

def test_legacy_prefer_forced_becomes_also_grab_forced():
    d = organization.normalize({"subtitle_hi_forced": "prefer_forced"})
    assert d["subtitle_hi_forced"] == "off" and d["subtitle_forced"] is True
    # an explicit toggle wins over the legacy value
    d = organization.normalize({"subtitle_hi_forced": "prefer_forced",
                                "subtitle_forced": False})
    assert d["subtitle_forced"] is False


def test_have_it_settings_default_like_bazarr():
    d = organization.normalize({})
    assert d["subtitle_use_embedded"] is True
    assert d["subtitle_forced"] is False
    assert not any(d["subtitle_ignore_" + k] for k in ("pgs", "vobsub", "ass"))
    d = organization.normalize({"subtitle_use_embedded": 0, "subtitle_ignore_pgs": 1})
    assert d["subtitle_use_embedded"] is False and d["subtitle_ignore_pgs"] is True


# ── providers: forced ────────────────────────────────────────────────────────

def _cand(ref, hi=False, forced=False, title="Movie.2024.1080p.WEB-GROUP", hash_match=True):
    return SubtitleCandidate(provider_id="scripted", language="en", hi=hi, forced=forced,
                             title=title, download_ref=ref, hash_match=hash_match,
                             download_count=10)


def test_manual_query_keeps_hi_beside_plain_but_never_forced():
    cands = [_cand("p"), _cand("h", hi=True), _cand("f", forced=True)]
    auto = flag_filter(cands, SubtitleQuery(language="en"))
    assert [c.download_ref for c in auto] == ["p"]
    manual = flag_filter(cands, SubtitleQuery(language="en", manual=True))
    assert [c.download_ref for c in manual] == ["p", "h"]
    forced = flag_filter(cands, SubtitleQuery(language="en", forced=True, manual=True))
    assert [c.download_ref for c in forced] == ["f"]


def test_opensubtitles_reads_foreign_parts_only_and_asks_for_the_right_kind(monkeypatch):
    from core.video.subtitles.providers.opensubtitles import OpenSubtitlesProvider
    p = OpenSubtitlesProvider()
    assert p.is_configured(lambda k, d=None: "key")
    calls = []
    found = {"data": [
        {"attributes": {"language": "en", "release": "Movie.Forced", "foreign_parts_only": True,
                        "files": [{"file_id": 1}]}},
        {"attributes": {"language": "en", "release": "Movie.Full", "foreign_parts_only": False,
                        "files": [{"file_id": 2}]}},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: calls.append(dict(params)) or found)
    forced = p.search(SubtitleQuery(identity={"imdb_id": "tt1"}, language="en", forced=True))
    assert [c.download_ref for c in forced] == [1] and forced[0].forced
    full = p.search(SubtitleQuery(identity={"imdb_id": "tt1"}, language="en"))
    assert [c.download_ref for c in full] == [2]
    assert [c["foreign_parts_only"] for c in calls] == ["only", "exclude"]


def test_subdl_reads_forced_off_the_release_text(monkeypatch):
    from core.video.subtitles.providers.subdl import SubDLProvider
    p = SubDLProvider()
    assert p.is_configured(lambda k, d=None: "key")
    payload = {"status": True, "subtitles": [
        {"language": "EN", "release_name": "Movie.2024.FORCED.WEB", "url": "/subtitle/1.zip"},
        {"language": "EN", "release_name": "Movie.2024.WEB", "url": "/subtitle/2.zip",
         "comment": "foreign parts only"},
        {"language": "EN", "release_name": "Movie.2024.BluRay", "url": "/subtitle/3.zip"},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    forced = p.search(SubtitleQuery(identity={"imdb_id": "tt1"}, language="en", forced=True))
    assert sorted(c.download_ref.rsplit("/", 1)[-1] for c in forced) == ["1.zip", "2.zip"]
    full = p.search(SubtitleQuery(identity={"imdb_id": "tt1"}, language="en"))
    assert [c.download_ref.rsplit("/", 1)[-1] for c in full] == ["3.zip"]


def test_whisper_never_offers_a_forced_track(tmp_path):
    from core.video.subtitles.providers.whisper import WhisperProvider
    p = WhisperProvider()
    p._model = "tiny"
    f = tmp_path / "v.mkv"
    f.write_bytes(b"x")
    assert p.search(SubtitleQuery(language="en", forced=True, file_path=str(f))) == []
    full = p.search(SubtitleQuery(language="en", hi=True, file_path=str(f)))
    assert len(full) == 1 and full[0].hi is False and full[0].forced is False


# ── worker / hook / backfill integration ─────────────────────────────────────

class ScriptedProvider(SubtitleProvider):
    id = "scripted"
    display_name = "Scripted"

    def __init__(self, candidates=(), texts=None):
        self._candidates = list(candidates)
        self._texts = dict(texts or {})
        self.queries = []

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return True

    def search(self, query):
        self.queries.append(query)
        return flag_filter(list(self._candidates), query)

    def download(self, candidate):
        return self._texts.get(candidate.download_ref)


class DiskFS:
    """Real listing (so sidecars written by the test are seen), recorded writes."""

    def __init__(self):
        self.texts = {}

    def list_dir(self, folder):
        return os.listdir(folder)

    def write_text(self, path, content):
        self.texts[path] = content
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _patch_registry(monkeypatch, provider):
    import core.video.subtitles.providers as pkg
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})


def _settings(**kw):
    s = {"download_subtitles": True, "subtitle_provider_order": '["scripted"]',
         "subtitle_worker_batch": 10, "subtitle_daily_quota": 20,
         "subtitle_min_score": 24.0, "subtitle_langs": "en"}
    s.update(kw)
    return s


def _movie_with_file(db, tmp_path, name="Movie.2024.1080p.WEB-GROUP.mkv"):
    folder = tmp_path / "lib" / "Movie (2024)"
    folder.mkdir(parents=True)
    path = folder / name
    path.write_bytes(os.urandom(200_000))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (42, 'p', 'm42', 'Movie', 603)")
        conn.execute("INSERT INTO media_files (movie_id, relative_path) VALUES (42, ?)",
                     (str(path),))
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _status(db, lang="en", hi=False, forced=False):
    for r in db.subtitle_get_for_video("movie", 42):
        if (r["language"], bool(r["hi"]), bool(r["forced"])) == (lang, hi, forced):
            return r["status"]
    return None


def test_worker_counts_an_eng_sidecar_and_never_fetches(db, tmp_path, monkeypatch):
    path = _movie_with_file(db, tmp_path)
    open(os.path.splitext(path)[0] + ".eng.srt", "w").write(ENGLISH)
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _patch_registry(monkeypatch, provider)
    db.subtitle_want("movie", 42, "en")
    worker.run_cycle(db, _settings(), DiskFS())
    assert _status(db) == "have" and provider.queries == []


def test_worker_counts_an_embedded_track_unless_switched_off(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path)
    monkeypatch.setattr(ex, "_probe_streams", lambda p: [_stream("subrip", "eng")])
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _patch_registry(monkeypatch, provider)
    db.subtitle_want("movie", 42, "en")
    worker.run_cycle(db, _settings(), DiskFS())
    assert _status(db) == "have" and provider.queries == []

    db.subtitle_mark("movie", 42, "en", "wanted", count_attempt=False)
    worker.run_cycle(db, _settings(subtitle_use_embedded=False), DiskFS())
    assert _status(db) == "have" and len(provider.queries) == 1   # fetched this time


def test_worker_fetches_a_forced_track_into_its_own_sidecar(db, tmp_path, monkeypatch):
    path = _movie_with_file(db, tmp_path)
    provider = ScriptedProvider([_cand("full"), _cand("forced", forced=True)],
                                {"full": "full srt", "forced": "forced srt"})
    _patch_registry(monkeypatch, provider)
    s = _settings(subtitle_forced=True)
    worker.queue_gaps(db, "movie", 42, worker.subtitle_gaps(db, s, "movie", 42))
    assert _status(db) == "wanted" and _status(db, forced=True) == "wanted"
    fs = DiskFS()
    worker.run_cycle(db, s, fs)
    stem = os.path.splitext(path)[0]
    assert fs.texts[stem + ".en.srt"] == "full srt"
    assert fs.texts[stem + ".en.forced.srt"] == "forced srt"
    assert _status(db, forced=True) == "have"


def test_forced_rows_are_residue_once_the_toggle_goes_off(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path)
    provider = ScriptedProvider([], {})
    _patch_registry(monkeypatch, provider)
    db.subtitle_want("movie", 42, "en", forced=True)
    db.subtitle_want("movie", 42, "es", forced=True, user_set=True)
    worker.run_cycle(db, _settings(subtitle_langs="en,es"), DiskFS())
    langs = {(r["language"], bool(r["forced"])) for r in db.subtitle_get_for_video("movie", 42)}
    assert ("en", True) not in langs        # system row: deleted
    assert ("es", True) in langs            # explicit pick: kept


def test_have_rows_are_rechecked_and_requeued_when_gone(db, tmp_path, monkeypatch):
    path = _movie_with_file(db, tmp_path)
    sidecar = os.path.splitext(path)[0] + ".en.srt"
    open(sidecar, "w").write(ENGLISH)
    provider = ScriptedProvider([], {})
    _patch_registry(monkeypatch, provider)
    db.subtitle_want("movie", 42, "en")
    db.subtitle_mark("movie", 42, "en", "have", count_attempt=False)
    db.subtitle_want("movie", 42, "fr", user_set=True)
    db.subtitle_mark("movie", 42, "fr", "have", count_attempt=False)

    stats = worker.run_upgrade_cycle(db, _settings(subtitle_langs="en"), DiskFS())
    assert _status(db) == "have"                      # still on disk
    assert _status(db, "fr") == "wanted"              # manual pick, gone: re-queued
    assert stats["requeued"] == 1 and provider.queries == []  # picks are never searched

    os.remove(sidecar)
    conn = db._get_connection()
    try:
        conn.execute("UPDATE subtitle_wanted SET last_upgrade_check_at=NULL")
        conn.commit()
    finally:
        conn.close()
    worker.run_upgrade_cycle(db, _settings(subtitle_langs="en"), DiskFS())
    assert _status(db) == "wanted"


def test_library_gaps_record_detected_and_embedded_subs_as_have(db, tmp_path, monkeypatch):
    path = _movie_with_file(db, tmp_path)
    open(os.path.splitext(path)[0] + ".srt", "w").write(SPANISH)   # unlabeled
    monkeypatch.setattr(ex, "_probe_streams", lambda p: [_stream("subrip", "ger", forced=1)])
    gaps = worker.subtitle_gaps(db, _settings(subtitle_langs="es,de,en", subtitle_forced=True),
                                "movie", 42)
    queued, marked = worker.queue_gaps(db, "movie", 42, gaps)
    assert _status(db, "es") == "have"
    assert _status(db, "de", forced=True) == "have"
    assert _status(db, "de") == "wanted"          # forced never covers the full track
    assert _status(db, "en") == "wanted"
    assert (queued, marked) == (4, 2)


class HookFS:
    def __init__(self, names):
        self.names = list(names)
        self.texts = {}

    def list_dir(self, folder):
        return list(self.names)

    def write_text(self, path, content):
        self.texts[str(path).replace("\\", "/")] = content


def test_hook_skips_languages_already_there_and_adds_forced_rows(db, monkeypatch):
    from core.video.download_monitor import write_subtitles_for
    calls = []

    def fake(query, provider_order, get_setting, on_download=None, skip_background=False):
        calls.append((query.language, query.forced))
        return "srt", "stub", None, 90.0

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", fake)
    dl = {"id": 7, "kind": "movie", "media_source": "tmdb", "media_id": "603",
          "search_ctx": "{}"}
    fs = HookFS(["M (2020).mkv", "M (2020).English.srt"])
    write_subtitles_for(db, dl, "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en,es", "subtitle_forced": True}, fs)
    assert sorted(calls) == [("en", True), ("es", False), ("es", True)]
    rows = {(r["language"], bool(r["forced"])): r["status"]
            for r in db.subtitle_get_for_video("download", 7)}
    assert rows == {("en", False): "have", ("en", True): "have",
                    ("es", False): "have", ("es", True): "have"}
    assert "/lib/M (2020)/M (2020).es.forced.srt" in fs.texts


def test_hook_never_fetches_residue_rows(db, monkeypatch):
    from core.video.download_monitor import write_subtitles_for
    calls = []

    def fake(query, provider_order, get_setting, on_download=None, skip_background=False):
        calls.append(query.language)
        return "srt", "stub", None, 90.0

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", fake)
    db.subtitle_want("download", 7, "fr")          # left over from an old language list
    dl = {"id": 7, "kind": "movie", "media_source": "tmdb", "media_id": "603",
          "search_ctx": "{}"}
    write_subtitles_for(db, dl, "/lib/M/M.mkv", {"subtitle_langs": "en"}, HookFS([]))
    assert calls == ["en"]


# ── test buttons ─────────────────────────────────────────────────────────────

def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "no", {}, None)


def test_subdl_test_button(monkeypatch):
    from core.video.subtitles.providers.subdl import SubDLProvider
    p = SubDLProvider()
    assert p.test(lambda k, d=None: d)[0] is False
    key = lambda k, d=None: "k"  # noqa: E731
    monkeypatch.setattr(p, "_search_api", lambda params: {"status": True, "subtitles": []})
    assert p.test(key) == (True, "SubDL key OK")
    monkeypatch.setattr(p, "_search_api", lambda params: {"status": False, "error": "Invalid API key"})
    assert p.test(key) == (False, "SubDL: Invalid API key")

    def boom(params):
        raise _http_error(403)
    monkeypatch.setattr(p, "_search_api", boom)
    assert p.test(key) == (False, "SubDL rejected the API key")


def test_subsource_test_button(monkeypatch):
    from core.video.subtitles.providers.subsource import SubSourceProvider
    p = SubSourceProvider()
    assert p.test(lambda k, d=None: d)[0] is False
    key = lambda k, d=None: "k"  # noqa: E731
    monkeypatch.setattr(p, "_api_get", lambda path, params: {"data": []})
    assert p.test(key) == (True, "SubSource key OK")

    def boom(path, params):
        raise _http_error(401)
    monkeypatch.setattr(p, "_api_get", boom)
    assert p.test(key) == (False, "SubSource rejected the API key")

    def down(path, params):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(p, "_api_get", down)
    assert p.test(key) == (False, "Could not reach SubSource: timed out")


def test_test_endpoint_reaches_subtitle_only_providers(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.enrichment.engine as eng_mod
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from core.video.subtitles.providers.subdl import SubDLProvider
    from core.video.subtitles.providers.subsource import SubSourceProvider
    from flask import Flask

    class FakeClient:
        enabled = True
        def match(self, *a, **k): return None
        def test(self): return (True, "ok")

    seen = {}
    monkeypatch.setattr(SubDLProvider, "_search_api",
                        lambda self, params: seen.setdefault("subdl", params) and {"status": True})
    monkeypatch.setattr(SubSourceProvider, "_api_get",
                        lambda self, path, params: {"data": []})
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("subdl_api_key", "the-key")
    videoapi._video_db = db
    eng_mod._engine = VideoEnrichmentEngine(db, {"tmdb": FakeClient()})
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        r = client.post("/api/video/enrichment/subdl/test").get_json()
        assert r["success"] is True and seen["subdl"]["api_key"] == "the-key"
        r = client.post("/api/video/enrichment/subsource/test").get_json()
        assert r["success"] is False and r["error"] == "No SubSource API key"
        assert client.post("/api/video/enrichment/nope/test").status_code == 404
    finally:
        videoapi._video_db = None
        eng_mod._engine = None
