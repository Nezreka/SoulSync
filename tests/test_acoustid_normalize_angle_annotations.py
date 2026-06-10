"""`_normalize` must strip ``<...>`` annotations like the AcoustID/MusicBrainz
vocalist credit ``澤野弘之 <Vocal: MIKA KOBAYASHI>``.

User report: a correct anime-OST track ("Attack on Titan" by "Sawano Hiroyuki")
was false-quarantined. AcoustID returned the artist as
``澤野弘之 <Vocal: MIKA KOBAYASHI>``. The kanji ``澤野弘之`` IS the artist and the
MusicBrainz alias bridge matches it — but `_normalize` stripped ``()`` and
``[]`` annotations, NOT ``<...>``, so the trailing "vocal mika kobayashi" words
diluted the alias comparison down to ~0.28 (below ARTIST_MATCH_THRESHOLD). That
in turn blocked the existing cross-script SKIP safety net (issue #797), which is
gated on ``artist_sim >= threshold``, so the file FAILED and was quarantined.

Stripping ``<...>`` restores the artist to ``澤野弘之`` so the alias match (and
thus the cross-script SKIP) works.
"""

from core.acoustid_verification import _normalize, _similarity


def test_normalize_strips_angle_bracket_vocalist_annotation():
    assert _normalize("澤野弘之 <Vocal: MIKA KOBAYASHI>") == "澤野弘之"


def test_normalize_strips_angle_brackets_latin():
    assert _normalize("Attack on Titan <TV Size>") == "attack on titan"


def test_vocalist_annotation_no_longer_dilutes_artist_similarity():
    # The kanji artist with a vocalist credit must compare as identical to the
    # bare kanji artist — this is what lets the alias bridge clear the threshold.
    assert _similarity("澤野弘之", "澤野弘之 <Vocal: MIKA KOBAYASHI>") == 1.0


def test_normalize_keeps_plain_text_untouched():
    # Guard: no angle brackets -> unchanged behaviour.
    assert _normalize("Sawano Hiroyuki") == "sawano hiroyuki"
