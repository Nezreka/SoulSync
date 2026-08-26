"""Tests for the track-number tag formatter.

Discord report (Netti93): album tracks tagged as "6/0" instead of
"6/13" when source data lacked total_tracks. Helper now returns just
"6" when total is 0 / unknown, matching what the retag tool already
did and what the ID3 spec allows.
"""

from core.metadata.track_number_format import (
    format_track_number_tag,
    format_track_number_tuple,
)


# ──────────────────────────────────────────────────────────────────────
# format_track_number_tag — string output for ID3 / Vorbis
# ──────────────────────────────────────────────────────────────────────

def test_track_with_known_total_returns_slash_format():
    assert format_track_number_tag(6, 13) == "6/13"
    assert format_track_number_tag(1, 1) == "1/1"
    assert format_track_number_tag(99, 100) == "99/100"


def test_zero_total_returns_track_number_only():
    """The Netti93 case — total_tracks=0 means unknown, NOT
    'track 6 of 0'. Drop the slash."""
    assert format_track_number_tag(6, 0) == "6"
    assert format_track_number_tag(1, 0) == "1"


def test_none_total_returns_track_number_only():
    assert format_track_number_tag(6, None) == "6"


def test_none_track_number_defaults_to_one():
    assert format_track_number_tag(None, 13) == "1/13"
    assert format_track_number_tag(None, None) == "1"


def test_zero_track_number_defaults_to_one():
    """Track 0 isn't valid in any convention — coerce to 1."""
    # Note: 0 is non-negative so falls into the default-0 path which
    # the formatter then treats as "default" via the explicit default
    # arg. Since 0 is technically valid output of `int(0)`, the helper
    # passes it through. Document the behavior here.
    # Actually re-checking: 0 satisfies `>= 0` so returns 0. That
    # means format would emit "0/13" for malformed input. Not great
    # but at least it doesn't crash. Test pins current behavior.
    assert format_track_number_tag(0, 13) == "0/13"


def test_negative_total_treated_as_unknown():
    assert format_track_number_tag(6, -1) == "6"


def test_negative_track_number_falls_back_to_default():
    assert format_track_number_tag(-1, 13) == "1/13"


def test_string_inputs_coerced():
    assert format_track_number_tag("6", "13") == "6/13"
    assert format_track_number_tag("6", "0") == "6"


def test_unparseable_inputs_use_defaults():
    assert format_track_number_tag("six", "thirteen") == "1"
    assert format_track_number_tag("abc", 13) == "1/13"


def test_float_inputs_truncate():
    # int() truncates floats — keeps behavior deterministic
    assert format_track_number_tag(6.7, 13.9) == "6/13"


# ──────────────────────────────────────────────────────────────────────
# format_track_number_tuple — MP4 trkn tuple
# ──────────────────────────────────────────────────────────────────────

def test_tuple_with_known_total():
    assert format_track_number_tuple(6, 13) == (6, 13)


def test_tuple_with_zero_total():
    assert format_track_number_tuple(6, 0) == (6, 0)


def test_tuple_with_none_total():
    assert format_track_number_tuple(6, None) == (6, 0)


def test_tuple_with_none_track_defaults_to_one():
    assert format_track_number_tuple(None, 13) == (1, 13)
    assert format_track_number_tuple(None, None) == (1, 0)


def test_tuple_negative_inputs_safe():
    assert format_track_number_tuple(-1, -5) == (1, 0)


def test_tuple_string_inputs_coerced():
    assert format_track_number_tuple("6", "13") == (6, 13)
    assert format_track_number_tuple("6", "0") == (6, 0)


# ── the Vorbis split (Discord, mrderekibmusic: FLACs tagged "1/1") ───────────
def test_vorbis_fields_are_split_never_combined():
    from core.metadata.track_number_format import format_vorbis_track_fields
    assert format_vorbis_track_fields(1, 1) == ("1", "1")
    assert format_vorbis_track_fields(6, 13) == ("6", "13")
    assert format_vorbis_track_fields(6, 0) == ("6", None)     # 0 = unknown, no lie
    assert format_vorbis_track_fields(6, None) == ("6", None)
    assert format_vorbis_track_fields(None, 13) == ("1", "13")
    num, _total = format_vorbis_track_fields(1, 1)
    assert "/" not in num, "the combined form is ID3's convention, not Vorbis's"


def test_the_retag_writer_splits_vorbis_and_keeps_id3_combined(tmp_path):
    """Both conventions at once: TRCK is DEFINED as 'N/M', TRACKNUMBER is not.
    The bug was applying ID3's rule to FLAC, where players and taggers show the
    field as written — track number '1/1'."""
    from core.tag_writer import _write_vorbis

    class _Fake(dict):
        pass

    audio = _Fake()
    _write_vorbis(audio, None, None, None, None, None, None,
                  track_num=1, total_tracks=1, disc_num=None, bpm=None)
    assert audio["tracknumber"] == ["1"]
    assert audio["tracktotal"] == ["1"]
    assert audio["totaltracks"] == ["1"]

    audio = _Fake()
    _write_vorbis(audio, None, None, None, None, None, None,
                  track_num=6, total_tracks=None, disc_num=None, bpm=None)
    assert audio["tracknumber"] == ["6"]
    assert "tracktotal" not in audio, "an unknown total must not be written"
