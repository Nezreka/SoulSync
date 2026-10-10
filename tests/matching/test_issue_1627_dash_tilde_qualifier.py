"""Dash/tilde-wrapped subtitle strip in audio verification (#1627).

``evaluate`` quarantined a correct file at title_sim ~0.55 < 0.70 because
``_DASH_QUALIFIER_RE`` required the qualifier to be dash-free through
end-of-string, so a provider-style wrapped subtitle like
``-Blue Forest Version-`` never stripped — while ``is_trailing_version_qualifier``
already classified the wrapped text as a version tail. The fix allows one
optional trailing dash on the qualifier and adds explicit tilde/wave-dash
wrapping delimiters (``~X~``, ``〜X〜``, ``～X～``).

Every row pins the DECISION (PASS), not just the similarity number: the bug
was a quarantine, so the test must prove the file is no longer quarantined.
The reverse-direction rows prove paren and dash wrappings meet in the middle
from either side.
"""

from __future__ import annotations

import pytest

from core.matching.audio_verification import (
    Decision,
    evaluate,
    normalize,
    similarity,
)


def _evaluate(recording_title: str, expected_title: str = "Blue Forest"):
    recordings = [{"title": recording_title, "artist": "Some Artist", "score": 1.0}]
    return evaluate(expected_title, "Some Artist", recordings, fingerprint_score=0.9)


def test_dash_wrapped_subtitle_passes():
    """The reported shape: '-Blue Forest Version-' stripped, file verified."""
    out = _evaluate("Blue Forest -Blue Forest Version-")
    assert out.decision is Decision.PASS
    assert out.title_sim >= 0.70


def test_spaced_dash_wrapped_subtitle_passes():
    out = _evaluate("Blue Forest - Blue Forest Version -")
    assert out.decision is Decision.PASS


def test_tilde_wrapped_subtitle_passes():
    out = _evaluate("Blue Forest ~Blue Forest Version~")
    assert out.decision is Decision.PASS


def test_wave_dash_wrapped_subtitle_passes():
    """U+301C wave dash wrapping, as written by Japanese taggers."""
    out = _evaluate("Blue Forest 〜Blue Forest Version〜")
    assert out.decision is Decision.PASS


def test_fullwidth_tilde_wrapped_subtitle_passes():
    """U+FF5E fullwidth tilde wrapping."""
    out = _evaluate("Blue Forest ～Blue Forest Version～")
    assert out.decision is Decision.PASS


# ---------------------------------------------------------------------------
# Unchanged behaviour: bare and bracketed forms keep passing.
# ---------------------------------------------------------------------------


def test_bare_title_still_passes():
    out = _evaluate("Blue Forest")
    assert out.decision is Decision.PASS


def test_parenthesised_qualifier_still_passes():
    out = _evaluate("Blue Forest (Blue Forest Version)")
    assert out.decision is Decision.PASS


def test_bracketed_qualifier_still_passes():
    out = _evaluate("Blue Forest [Blue Forest Version]")
    assert out.decision is Decision.PASS


def test_plain_dash_tail_still_passes():
    out = _evaluate("Blue Forest - Remastered 2011")
    assert out.decision is Decision.PASS


# ---------------------------------------------------------------------------
# Reverse direction: paren and dash wrappings meet from either side.
# ---------------------------------------------------------------------------


def test_paren_expected_dash_wrapped_recording_passes():
    out = _evaluate("Blue Forest -Blue Forest Version-",
                    expected_title="Blue Forest (Blue Forest Version)")
    assert out.decision is Decision.PASS


def test_dash_wrapped_expected_paren_recording_passes():
    out = _evaluate("Blue Forest (Blue Forest Version)",
                    expected_title="Blue Forest -Blue Forest Version-")
    assert out.decision is Decision.PASS


# ---------------------------------------------------------------------------
# Over-strip guards: the new delimiters must not eat real titles.
# ---------------------------------------------------------------------------


def test_intra_word_hyphen_never_strips():
    """The Post-Remix lesson: the opening delimiter still needs whitespace
    adjacency, so an intra-word hyphen is punctuation, not a tail."""
    assert normalize("Post-Remix") == "postremix"


def test_distinct_track_dash_tail_never_strips():
    """'Pt. 2' / 'Interlude' name a different track — the version-tail veto
    in is_trailing_version_qualifier still outranks the new trailing dash."""
    assert normalize("Song - Pt. 2") == "song pt 2"
    assert normalize("Song - Interlude") == "song interlude"


def test_bare_tilde_inside_title_never_strips():
    """A '~' without the wrapping pair (or without preceding whitespace) is
    punctuation, not a version delimiter."""
    assert normalize("Hello~World") == "helloworld"


def test_non_version_tilde_wrap_never_strips():
    """Even a well-formed '~X~' wrap only strips when X reads as a version
    qualifier — a real subtitle survives via the dual reading."""
    assert similarity("Song ~Stylized Subtitle~", "Song") < 1.0
    assert normalize("Song ~Stylized Subtitle~", strip_version_tail=False) == \
        "song stylized subtitle"
