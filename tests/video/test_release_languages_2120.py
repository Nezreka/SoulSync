"""#2120: the downloads page shows what languages a release carries.

the release name already says it (MULTi, VFF, VOSTFR, FRENCH.SUBS), but only
the audio half was parsed and none of it reached the download rows, so a
grab's audio and subtitle languages were invisible on the page.
"""

import pytest

from api.video.downloads import annotate_release_languages
from core.video.release_parse import parse_subtitle_tags, release_languages


@pytest.mark.parametrize("name, subs, subbed", [
    ("Movie.2020.VOSTFR.1080p.WEB", ["fr"], True),
    ("Show.S01E02.FRENCH.SUBS.720p.WEB", ["fr"], True),
    ("Show.S02E01.SUBFRENCH.720p.WEB", ["fr"], True),
    ("Film.2021.1080p.WEB.ESub", ["en"], True),
    ("Film.2019.English.Subtitles.1080p.BluRay", ["en"], True),
    ("Film.2019.MULTiSUBS.1080p.BluRay", [], True),
    # audio tags are not subtitles
    ("Dune.2021.MULTi.VFF.2160p.WEB.x265-GRP", [], False),
    ("Movie.2019.FRENCH.1080p.BluRay", [], False),
    # a title word is not a tag
    ("The.Subs.2020.1080p.WEB", [], False),
])
def test_subtitle_tags(name, subs, subbed):
    assert parse_subtitle_tags(name) == (subs, subbed)


def test_release_languages_has_both_halves():
    assert release_languages("Primal.S02E03.MULTi.VFF.1080p.WEB.H264-GRP") == {
        "audio": ["fr"], "multi": True, "subs": [], "subbed": False}
    assert release_languages("Primal.S02E03.VOSTFR.1080p.WEB.H264-GRP") == {
        "audio": [], "multi": False, "subs": ["fr"], "subbed": True}


def test_download_rows_carry_their_languages():
    rows = [
        {"source": "torrent", "release_title": "Dune.2021.MULTi.VOSTFR.1080p.WEB.x264-GRP"},
        {"source": "youtube", "release_title": "Some.Video.2021.1080p"},
        {"source": "soulseek", "release_title": None},
    ]
    annotate_release_languages(rows)
    assert rows[0]["release_langs"] == {"audio": [], "multi": True, "subs": ["fr"], "subbed": True}
    assert "release_langs" not in rows[1] and "release_langs" not in rows[2]
