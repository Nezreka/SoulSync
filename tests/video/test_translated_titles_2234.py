"""A release named in any language TMDB knows the title in is the title (#2234).

Thibs searched Rocco and His Brothers (1960) in Manual search by its french
title, "Rocco et ses frères". every french release was refused as "Wrong
title (Rocco et ses freres — wanted ['Rocco and His Brothers', 'Rocco e i
suoi fratelli', 'Rocco en Zijn Broers', …])". the accepted names were TMDB's
english name, the italian original and alternative_titles, and TMDB keeps a
film's own french title in its translations, which were never fetched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# the shape TMDB sends for /movie/8422?append_to_response=alternative_titles,translations
ROCCO = {
    "id": 8422, "title": "Rocco and His Brothers", "original_title": "Rocco e i suoi fratelli",
    "release_date": "1960-09-06",
    "alternative_titles": {"titles": [
        {"iso_3166_1": "NL", "title": "Rocco en Zijn Broers", "type": ""},
        {"iso_3166_1": "US", "title": "Rocco and His Brothers", "type": ""},
    ]},
    "translations": {"translations": [
        {"iso_3166_1": "US", "iso_639_1": "en", "english_name": "English",
         "data": {"title": "Rocco and His Brothers", "overview": "..."}},
        {"iso_3166_1": "IT", "iso_639_1": "it", "english_name": "Italian",
         "data": {"title": "", "overview": "..."}},     # same as the original: empty
        {"iso_3166_1": "FR", "iso_639_1": "fr", "english_name": "French",
         "data": {"title": "Rocco et ses frères", "overview": "..."}},
        {"iso_3166_1": "DE", "iso_639_1": "de", "english_name": "German",
         "data": {"title": "Rocco und seine Brüder", "overview": "..."}},
        {"iso_3166_1": "NL", "iso_639_1": "nl", "english_name": "Dutch",
         "data": {"title": "Rocco en zijn broers", "overview": "..."}},
    ]},
}
# Up (2009): its german title is one common word
UP = {
    "id": 14160, "title": "Up", "original_title": "Up", "release_date": "2009-05-28",
    "alternative_titles": {"titles": []},
    "translations": {"translations": [
        {"iso_3166_1": "DE", "iso_639_1": "de", "data": {"title": "Oben"}},
        {"iso_3166_1": "FR", "iso_639_1": "fr", "data": {"title": "Là-haut"}},
    ]},
}
# a show: TMDB's tv translations carry "name", not "title"
DARK = {
    "id": 70523, "name": "Dark", "original_name": "Dark", "first_air_date": "2017-12-01",
    "alternative_titles": {"results": []},
    "translations": {"translations": [
        {"iso_3166_1": "FR", "iso_639_1": "fr", "data": {"name": "Dark : les origines"}},
        {"iso_3166_1": "RU", "iso_639_1": "ru", "data": {"name": "Тьма"}},
    ]},
}
_RAW = {8422: ("movie", ROCCO), 14160: ("movie", UP), 70523: ("show", DARK)}

SCREENSHOT = [
    "Rocco.et.ses.Frères.Luchino.Visconti.1960.MULTI.BDRip.1080p.x264.AAC-DarkJuju",
    "Rocco.et.ses.freres.1960.FRENCH.1080p.AAC.x264-mHDgz",
    "Rocco et ses frères (french)",
]


class _Tmdb:
    def title_identity(self, kind, tmdb_id):
        from core.video.enrichment.clients import parse_title_identity
        k, raw = _RAW[int(tmdb_id)]
        return parse_title_identity(kind, raw)


@pytest.fixture()
def engine(monkeypatch):
    """the real enrichment engine's title methods over a fake TMDB client"""
    import core.video.enrichment.engine as eng_mod
    from core.video.enrichment.engine import VideoEnrichmentEngine
    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    eng._cache = eng_mod.TTLCache(maxsize=64, ttl=60)
    eng.workers = {"tmdb": SimpleNamespace(enabled=True, client=_Tmdb())}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: eng)
    return eng


def _ok(name, want, year, scope="movie"):
    from core.video.quality_eval import _scope_ok
    from core.video.release_parse import parse_release
    _, why = _scope_ok(parse_release(name), scope, None, None, year, want)
    return why is None, why


# ── where the names come from ───────────────────────────────────────────────

def test_identity_carries_every_language_title():
    from core.video.enrichment.clients import parse_title_identity
    ident = parse_title_identity("movie", ROCCO)
    assert "Rocco et ses frères" in ident["translations"]
    assert "Rocco und seine Brüder" in ident["translations"]
    # no blanks, nothing already named (english, the dutch alias in other case)
    assert "" not in ident["translations"]
    assert "Rocco and His Brothers" not in ident["translations"]
    assert "Rocco en zijn broers" not in ident["translations"]


def test_show_translations_read_name():
    from core.video.enrichment.clients import parse_title_identity
    assert parse_title_identity("show", DARK)["translations"] == ["Dark : les origines", "Тьма"]


def test_identity_without_translations_reads_as_before():
    from core.video.enrichment.clients import parse_title_identity
    raw = {k: v for k, v in ROCCO.items() if k != "translations"}
    assert "translations" not in parse_title_identity("movie", raw)


def test_tmdb_is_asked_for_translations(monkeypatch):
    import requests

    from core.video.enrichment.clients import TMDBClient
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return ROCCO

    def fake_get(url, params=None, timeout=None):
        seen.update(params or {})
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    c = TMDBClient.__new__(TMDBClient)
    c.api_key = "k"
    ident = c.title_identity("movie", 8422)
    assert "translations" in seen["append_to_response"].split(",")
    assert "Rocco et ses frères" in ident["translations"]


def test_accepted_names_include_translations(engine):
    names = engine.alt_titles_for("movie", 8422)
    assert names[:2] == ["Rocco and His Brothers", "Rocco e i suoi fratelli"]
    assert "Rocco et ses frères" in names and "Rocco en Zijn Broers" in names


def test_a_one_word_translation_is_held_to_the_year(engine):
    names = engine.alt_titles_for("movie", 14160)
    assert "Oben (2009)" in names and "Oben" not in names
    assert "Là-haut" in names


def test_a_one_word_translation_with_no_year_is_left_out(engine):
    raw = dict(UP, release_date="")
    _RAW[99] = ("movie", raw)
    try:
        names = engine.alt_titles_for("movie", 99)
    finally:
        _RAW.pop(99)
    assert not any(n.startswith("Oben") for n in names)


# ── the gate, with the names the search really has ──────────────────────────

@pytest.mark.parametrize("name", SCREENSHOT)
def test_the_three_releases_in_the_screenshot_are_the_film(engine, name):
    want = engine.alt_titles_for("movie", 8422)
    ok, why = _ok(name, want, 1960)
    assert ok, why


@pytest.mark.parametrize("name", [
    "Rocco.and.His.Brothers.1960.MULTI.VFF.1080p.BluRay.FLAC.2.0.x264-pathecroute",
    "Rocco.e.i.suoi.fratelli.1960.ITALIAN.1080p.BluRay.x264-GRP",
    "Rocco.und.seine.Bruder.1960.GERMAN.1080p.BluRay.x264-GRP",
])
def test_every_other_name_still_matches(engine, name):
    ok, why = _ok(name, engine.alt_titles_for("movie", 8422), 1960)
    assert ok, why


@pytest.mark.parametrize("name", [
    # a different film with a similar name
    "Rocco.et.ses.Freres.de.Sang.1960.1080p.BluRay.x264-GRP",
    "Rocco.2002.FRENCH.1080p.WEB.x264-GRP",
    "Rocco.and.the.Sisters.1960.1080p.BluRay.x264-GRP",
    # the right name, a different year: the year gate still runs
    "Rocco.et.ses.freres.1975.FRENCH.1080p.x264-GRP",
])
def test_a_different_film_is_still_refused(engine, name):
    ok, why = _ok(name, engine.alt_titles_for("movie", 8422), 1960)
    assert not ok, name


def test_extra_words_need_the_exact_year(engine):
    want = engine.alt_titles_for("movie", 8422)
    # the director's name with the next year: not proof enough
    ok, why = _ok("Rocco.et.ses.Freres.Luchino.Visconti.1961.1080p.x264-GRP", want, 1960)
    assert not ok and why.startswith("Wrong title")
    # no year at all: never
    ok, _ = _ok("Rocco.et.ses.Freres.Luchino.Visconti.1080p.x264-GRP", want, 1960)
    assert not ok


@pytest.mark.parametrize("name,want,year", [
    ("Wicked.For.Good.2025.1080p.WEB.x264-GRP", ["Wicked"], 2025),          # one-word title
    ("Dune.Part.Two.2021.1080p.WEB.x264-GRP", ["Dune Prophecy"], 2021),
    ("Kill.Bill.Vol.2.2003.1080p.BluRay.x264-GRP", ["Kill Bill"], 2003),
    ("Rocky.Balboa.Returns.1976.1080p.x264-GRP", ["Rocky Balboa"], 1976),
    ("Rocco.et.ses.Freres.II.1960.1080p.x264-GRP", ["Rocco et ses frères"], 1960),
    ("Rocco.et.ses.Freres.Part.Two.1960.1080p.x264-GRP", ["Rocco et ses frères"], 1960),
    ("Rocco.et.ses.Freres.Alpha.Bravo.Charlie.Delta.1960.1080p.x264-GRP", ["Rocco et ses frères"], 1960),
])
def test_sequels_never_pass_as_extra_words(name, want, year):
    ok, _ = _ok(name, want, year)
    assert not ok, name


def test_one_word_translation_is_guarded_by_year(engine):
    want = engine.alt_titles_for("movie", 14160)
    assert _ok("Oben.2009.GERMAN.1080p.BluRay.x264-GRP", want, 2009)[0]
    # the same word with no year is any film called that
    assert not _ok("Oben.GERMAN.1080p.BluRay.x264-GRP", want, 2009)[0]
    assert not _ok("Oben.2015.GERMAN.1080p.BluRay.x264-GRP", want, 2009)[0]
    # and it takes no extra words: one word plus anything is another film
    assert not _ok("Oben.Ohne.2009.GERMAN.1080p.BluRay.x264-GRP", want, 2009)[0]


def test_a_one_word_title_takes_no_extra_words_even_with_the_year():
    # 'Up Something 2009' is another film, whatever its year
    assert not _ok("Up.Something.2009.1080p.BluRay.x264-GRP", ["Up"], 2009)[0]
    # an edition or language word is still the film
    assert _ok("Up.Extended.2009.1080p.BluRay.x264-GRP", ["Up"], 2009)[0]
    assert _ok("Up.French.2009.1080p.BluRay.x264-GRP", ["Up"], 2009)[0]


def test_show_releases_in_a_translated_name(engine):
    want = engine.alt_titles_for("show", 70523)
    from core.video.quality_eval import _scope_ok
    from core.video.release_parse import parse_release
    _, why = _scope_ok(parse_release("Dark.Les.Origines.S01E01.FRENCH.1080p.WEB.x264-GRP"),
                       "episode", 1, 1, 2017, want)
    assert why is None, why
    _, why = _scope_ok(parse_release("Dark.Matter.S01E01.1080p.WEB.x264-GRP"),
                       "episode", 1, 1, 2017, want)
    assert why and why.startswith("Wrong title")
    # "Тьма" is one word: only with the show's year
    assert "Тьма (2017)" in want


def test_the_refusal_names_a_few_not_all():
    from core.video.quality_eval import _wanted_names
    names = ["A b", "C d", "E f", "G h", "I j", "K l"]
    assert _wanted_names(names) == "['A b', 'C d', 'E f', 'G h'] +2 more"
    assert _wanted_names(["A b"]) == "['A b']"
    assert _wanted_names("This Show") == "This Show"


# ── the words typed into Manual search ──────────────────────────────────────

def test_typed_query_is_an_accepted_name():
    from api.video.downloads import _want_titles
    body = {"title": "Rocco and His Brothers", "titles": ["Rocco and His Brothers"],
            "year": 1960, "query": "Rocco et ses frères"}
    want = _want_titles(body)
    assert want == ["Rocco and His Brothers", "Rocco et ses frères"]
    for name in SCREENSHOT:
        assert _ok(name, want, 1960)[0], name
    # the year gate still runs on it
    assert not _ok("Rocco.et.ses.freres.1975.FRENCH.1080p.x264-GRP", want, 1960)[0]


def test_typed_one_word_query_needs_the_year():
    from api.video.downloads import _want_titles
    want = _want_titles({"title": "Up", "year": 2009, "query": "Oben"})
    assert want == ["Up", "Oben (2009)"]
    assert not _ok("Oben.GERMAN.1080p.x264-GRP", want, 2009)[0]
    assert _ok("Oben.2009.GERMAN.1080p.x264-GRP", want, 2009)[0]
    # a one-word query with no year to hold it to adds nothing
    assert _want_titles({"title": "Up", "query": "Oben"}) == "Up"


def test_no_typed_query_reads_as_before():
    from api.video.downloads import _want_titles
    assert _want_titles({"title": "Up", "titles": ["Up", "Là-haut"]}) == ["Up", "Là-haut"]
    assert _want_titles({"title": "Up"}) == "Up"
