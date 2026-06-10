"""Shared audio-verification decision core: normalize() + evaluate().

One place for normalization + the PASS/SKIP/FAIL decision used by BOTH import-time
verification and the library AcoustID scan, so the two paths can't drift apart.
"""

from core.matching.audio_verification import normalize


def test_normalize_strips_paren_bracket_angle_and_keeps_cjk():
    assert normalize("澤野弘之 <Vocal: MIKA KOBAYASHI>") == "澤野弘之"
    assert normalize("Clarity (Live at X) [Remastered]") == "clarity"
    assert normalize("Attack on Titan <TV Size>") == "attack on titan"


def test_normalize_strips_version_and_featuring():
    assert normalize("In My Feelings - Instrumental") == "in my feelings"
    assert normalize("Song feat. Someone") == "song"


def test_normalize_keeps_plain_text():
    assert normalize("Sawano Hiroyuki") == "sawano hiroyuki"
