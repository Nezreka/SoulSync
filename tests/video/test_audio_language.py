"""audio language for dubbed / MULTI releases (Thibs' request).

subtitles had a preferred language, the release itself didn't, so there was
no way to ask for a MULTI or dubbed copy. the profile now carries audio
languages with off / prefer / require, read from the release name's tags.
"""

import pytest

from core.video import quality_profile
from core.video.quality_eval import evaluate_release
from core.video.release_parse import parse_languages, parse_release


@pytest.mark.parametrize("name, codes, multi", [
    ("Dune.2021.MULTi.VFF.2160p.WEB.x265-GRP", ["fr"], True),
    ("Dune.2021.TRUEFRENCH.1080p.BluRay.x264", ["fr"], False),
    ("Film.2019.German.DL.1080p.BluRay.x264-GRP", ["de"], True),
    ("[SubsPlease] Jujutsu Kaisen - 59 (1080p) [Dual Audio]", [], True),
    ("Movie.2021.iTA.ENG.1080p.WEB", ["it", "en"], False),
    # subs, not audio
    ("Movie.2020.VOSTFR.1080p.WEB", [], False),
    ("Show.S01E02.FRENCH.SUBS.720p.WEB", [], False),
    # WEB-DL is not dual language
    ("Movie.2021.1080p.WEB-DL.DDP5.1", [], False),
    # a title word is not a tag
    ("The.French.Dispatch.2021.1080p.WEB", [], False),
])
def test_tags(name, codes, multi):
    assert parse_languages(name) == (codes, multi)


def test_profile_keeps_valid_languages_only():
    p = quality_profile.normalize({"audio_language_mode": "require",
                                   "audio_languages": ["FR", "xx", "de", "fr"]})
    assert p["audio_language_mode"] == "require" and p["audio_languages"] == ["fr", "de"]
    d = quality_profile.normalize({"audio_language_mode": "loud"})
    assert d["audio_language_mode"] == "off" and d["audio_languages"] == []


def _profile(mode, langs=("fr",)):
    return quality_profile.normalize({"audio_language_mode": mode, "audio_languages": list(langs)})


def _eval(name, profile):
    return evaluate_release(parse_release(name), profile, scope="movie", want_year=2021,
                            want_title="Dune")


def test_prefer_ranks_the_dub_over_a_better_original():
    p = _profile("prefer")
    dub = _eval("Dune.2021.MULTi.720p.WEB.x264-GRP", p)
    orig = _eval("Dune.2021.1080p.WEB.x264-GRP", p)
    assert dub["accepted"] and orig["accepted"]
    assert dub["score"] > orig["score"]


def test_require_skips_untagged_and_keeps_multi():
    p = _profile("require")
    assert not _eval("Dune.2021.1080p.WEB.x264-GRP", p)["accepted"]
    assert "FR" in _eval("Dune.2021.1080p.WEB.x264-GRP", p)["rejected"]
    assert _eval("Dune.2021.MULTi.1080p.WEB.x264-GRP", p)["accepted"]
    assert _eval("Dune.2021.FRENCH.1080p.WEB.x264-GRP", p)["accepted"]
    assert not _eval("Dune.2021.GERMAN.1080p.WEB.x264-GRP", p)["accepted"]


def test_off_changes_nothing():
    p = _profile("off")
    a = _eval("Dune.2021.MULTi.1080p.WEB.x264-GRP", p)
    b = _eval("Dune.2021.1080p.WEB.x264-GRP", p)
    assert a["accepted"] and b["accepted"] and a["score"] == b["score"]
