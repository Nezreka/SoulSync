"""the 100 was taken as 100 days of deception.

the korean show's own-language title ('100일의 거짓말') folded to just
'100' (the hangul dropped, the digits kept), and 'The 100' folds to '100'
too, so every scene release of the 100 passed the title gate as that show.
the importer then filed them, because it never checked the title again.
"""

from __future__ import annotations

import json

from core.video import download_monitor as mon
from core.video.importer import plan_import
from core.video.mediainfo import parse_ffprobe
from core.video.release_parse import normalize_title, titles_match

THE_100_EP = "The.100.S01E01.1080p.BluRay.x265-RARBG"
THE_100_PACK = "The.100.S01.1080p.BluRay.x265-RARBG"
DECEPTION_EP = "100.Days.of.Deception.S01E01.1080p.WEB-DL.H264-GRP"
DECEPTION = ["100 Days of Deception", "100일의 거짓말", "100日の嘘"]


# ── the title gate ────────────────────────────────────────────────────────────
def test_the_100_never_matches_100_days_of_deception():
    for rel in (THE_100_EP, THE_100_PACK):
        assert not titles_match(rel, "100 Days of Deception")
        assert not titles_match(rel, DECEPTION)


def test_100_days_of_deception_never_matches_the_100():
    assert not titles_match(DECEPTION_EP, "The 100")
    assert not titles_match(DECEPTION_EP, ["The 100", "The Hundred"])


def test_each_show_still_matches_its_own_releases():
    assert titles_match(DECEPTION_EP, DECEPTION)
    assert titles_match(THE_100_EP, "The 100")
    assert titles_match(THE_100_PACK, ["The 100"])


def test_a_non_latin_title_keeps_its_letters_not_just_its_number():
    assert normalize_title("100일의 거짓말") != normalize_title("The 100")
    assert normalize_title("100日の嘘") != "100"
    # a latin title with accents folds the same as ever
    assert normalize_title("Rocco et ses frères") == "rocco et ses freres"
    assert normalize_title("The Dark Knight") == "dark knight"


def test_a_release_named_in_the_original_script_still_matches():
    assert titles_match("100일의 거짓말.S01E01.1080p.WEB-DL", DECEPTION)
    assert titles_match("進撃の巨人 S01E01 1080p", ["Attack on Titan", "進撃の巨人"])


def test_year_suffixed_titles_still_match():
    assert titles_match("Doctor.Who.2005.S01E01.1080p.BluRay.x264-GRP", "Doctor Who (2005)")
    assert titles_match("Doctor.Who.2005.S01E01.1080p.BluRay.x264-GRP", "Doctor Who 2005")
    assert not titles_match("Doctor.Who.1963.S01E01.DVDRip", "Doctor Who (2005)")


def test_alternative_titles_still_match():
    assert titles_match("God.Particle.2018.1080p.WEB-DL", ["The Cloverfield Paradox", "God Particle"])
    assert titles_match("Shingeki.no.Kyojin.S01E01.1080p", ["Attack on Titan", "Shingeki no Kyojin"])


def test_anime_absolute_numbering_still_matches():
    assert titles_match("[SubsPlease] One Piece - 1071 (1080p) [ABCD1234].mkv", "One Piece")
    assert titles_match("One.Piece.E1071.1080p.WEB-DL", "One Piece")
    assert not titles_match("[SubsPlease] The 100 - 01 (1080p)", DECEPTION)
    assert not titles_match("[Erai-raws] One Piece Film Red - 01 (1080p)", "One Piece")


# ── the importer refuses a file that names another show ───────────────────────
def _dl(release, titles=DECEPTION, **kw):
    base = {"id": 7, "kind": "show", "source": "torrent", "title": "100 Days of Deception",
            "release_title": release, "target_dir": "/tv", "size_bytes": 2_000_000_000,
            "search_ctx": json.dumps({"scope": "episode", "title": "100 Days of Deception",
                                      "season": 1, "episode": 1, "titles": titles})}
    base.update(kw)
    return base


def _plan(dl, src, probe=None, **kw):
    return plan_import(dl, src, list_dir=lambda _p: [], probe=probe, **kw)


def test_importer_refuses_the_100_filed_as_100_days_of_deception():
    plan = _plan(_dl(THE_100_EP), "/dl/" + THE_100_EP + ".mkv")
    assert plan["action"] == "reject"
    assert plan["bad_release"] is True
    assert "The 100" in plan["reason"]


def test_importer_refuses_a_pack_episode_of_the_100():
    plan = _plan(_dl(THE_100_PACK), "/dl/The.100.S01/" + THE_100_EP + ".mkv")
    assert plan["action"] == "reject" and plan["bad_release"] is True


def test_importer_refuses_a_file_whose_own_name_is_another_show():
    # the release name looked right, the file inside is the 100
    plan = _plan(_dl(DECEPTION_EP), "/dl/" + THE_100_EP + ".mkv")
    assert plan["action"] == "reject" and plan["bad_release"] is True


def test_importer_refuses_a_file_whose_title_tag_is_another_show():
    probe = parse_ffprobe({"format": {"duration": "2700", "tags": {"title": THE_100_EP}},
                           "streams": [{"codec_type": "video", "width": 1920, "height": 1080}]})
    assert probe["container_title"] == THE_100_EP
    plan = _plan(_dl(DECEPTION_EP), "/dl/episode01.mkv", probe=probe)
    assert plan["action"] == "reject" and plan["bad_release"] is True


def test_importer_takes_the_right_show():
    plan = _plan(_dl(DECEPTION_EP), "/dl/" + DECEPTION_EP + ".mkv")
    assert plan["action"] == "import"


def test_a_plain_file_name_or_episode_title_tag_never_refuses():
    probe = parse_ffprobe({"format": {"duration": "2700", "tags": {"title": "Pilot"}},
                           "streams": [{"codec_type": "video", "width": 1920, "height": 1080}]})
    plan = _plan(_dl(DECEPTION_EP), "/dl/ep01.mkv", probe=probe)
    assert plan["action"] == "import"


def test_explicit_want_titles_win_over_the_context():
    dl = _dl(THE_100_EP, titles=None)
    assert _plan(dl, "/dl/x.mkv")["action"] == "import"          # nothing known: no check
    plan = _plan(dl, "/dl/x.mkv", want_titles=DECEPTION)
    assert plan["action"] == "reject" and plan["bad_release"] is True


def test_a_forced_manual_placement_is_never_refused_for_its_name():
    plan = plan_import(_dl(THE_100_EP), "/dl/" + THE_100_EP + ".mkv", list_dir=lambda _p: [],
                       force=True, override={"scope": "episode", "title": "100 Days of Deception",
                                             "season": 1, "episode": 1, "target_dir": "/tv"})
    assert plan["action"] != "reject"


def test_monitor_import_titles_reads_the_search_list():
    class _DB:
        def show_tmdb_id(self, _mid):
            return 5
    assert mon._import_titles(_DB(), _dl(THE_100_EP)) == DECEPTION
    assert mon._import_titles(_DB(), {"kind": "youtube", "search_ctx": "{}"}) == []
