"""SoulID kinded_v2 — the generator.

kinded_v2 (core/soulid_worker.SOULID_VERSION) replaced the pre-v2 scheme:

* the artist id was derived from a provider artist id found by a fuzzy
  Deezer/iTunes search, with library-dependent fallbacks — two installs
  routinely derived different ids for the same artist. v2 mints the
  artist id from the normalized name alone;
* the hash input was the parts concatenated with no separator and no
  entity kind, so an album and a song with the same artist + title
  shared an id. v2 hashes a positional, kinded, versioned input;
* the normalizer deleted every character outside [a-z0-9], hashing
  non-Latin names to '' or fragments. v2 keeps Unicode letters/digits.

Upstream's batch-minting and cutover tests run raw SQL against the legacy
``artists/albums/tracks`` tables; they live on as their lib2 port in
tests/library2/test_soulid_worker_on_lib2.py.
"""

from __future__ import annotations

import re

import pytest

from core.soulid_worker import generate_soul_id


def test_ids_are_deterministic_and_well_formed():
    a = generate_soul_id("album", "Beyoncé", "Renaissance")
    b = generate_soul_id("album", "Beyoncé", "Renaissance")
    assert a == b
    assert re.fullmatch(r"soul_[0-9a-f]{16}", a)


def test_accents_do_not_change_the_id():
    assert generate_soul_id("artist", "Beyoncé") == \
        generate_soul_id("artist", "Beyonce")


def test_the_kind_is_part_of_the_id():
    """Pre-v2, an album and a song with the same artist + title hashed to
    the same id. They must not any more."""
    assert generate_soul_id("album", "Michael Jackson", "Thriller") != \
        generate_soul_id("track", "Michael Jackson", "Thriller")


def test_parts_cannot_bleed_into_each_other():
    """("AB", "C") and ("A", "BC") were the same concatenated input."""
    assert generate_soul_id("album", "AB", "C") != \
        generate_soul_id("album", "A", "BC")


def test_non_latin_names_mint_real_ids():
    assert generate_soul_id("artist", "Кино")
    assert generate_soul_id("artist", "米津玄師")
    # Two different Cyrillic albums by one artist are different albums.
    assert generate_soul_id("album", "Кино", "Звезда по имени Солнце") != \
        generate_soul_id("album", "Кино", "Группа крови")


def test_a_part_that_normalizes_to_nothing_yields_no_id():
    # Whitespace alone has no fallback content either — no id.
    assert generate_soul_id("artist", "   ") == ""
    assert generate_soul_id("album", "Кино", "   ") == ""


def test_symbol_only_names_still_mint_one_deterministic_id():
    """The band "!!!" and Sigur Rós' "( )" have no letters to hash, but
    they must not become placeholders the export hides forever: the
    normalizer falls back to the raw text, so every install agrees."""
    assert generate_soul_id("artist", "!!!")
    assert generate_soul_id("artist", "!!!") == \
        generate_soul_id("artist", "!!!")
    assert generate_soul_id("album", "Sigur Rós", "( )")


def test_marks_that_are_part_of_the_letter_survive():
    """Accents come off Latin/Greek/Cyrillic bases, but a kana dakuten,
    a Devanagari vowel sign and a Thai tone are part of the letter —
    stripping them collides different words onto one id."""
    from core.soulid_worker import normalize_for_soul_id
    assert normalize_for_soul_id("が") == "が"
    assert normalize_for_soul_id("が") != normalize_for_soul_id("か")
    assert normalize_for_soul_id("हिन्दी") == "हिन्दी"
    assert normalize_for_soul_id("ที่") == "ที่"
    assert generate_soul_id("artist", "バンド") != \
        generate_soul_id("artist", "ハント")


def test_optional_vowel_marks_and_invisibles_come_off():
    """The other side of the mark rule: niqqud and harakat are OPTIONAL
    vowel marks (most Hebrew/Arabic text is written without them), and
    invisible format chars are source noise. Keeping any of them would
    split one artist into two ids."""
    from core.soulid_worker import normalize_for_soul_id
    assert normalize_for_soul_id("שָׁלוֹם") == normalize_for_soul_id("שלום")
    assert normalize_for_soul_id("مُحَمَّد") == normalize_for_soul_id("محمد")
    assert normalize_for_soul_id("שלום\u200f") == normalize_for_soul_id("שלום")
    # Hamza folds onto its carrier — a decision, not an accident:
    # sources disagree on hamza spelling constantly.
    assert normalize_for_soul_id("أحمد") == normalize_for_soul_id("احمد")
    # A parenthetical suffix must not leak into the symbol fallback.
    assert normalize_for_soul_id("!!! (Deluxe)") == \
        normalize_for_soul_id("!!!")
    # Nor may an invisible format char, and an invisibles-only name
    # has no content to fall back to.
    assert normalize_for_soul_id("!!!\u200f") == normalize_for_soul_id("!!!")
    assert generate_soul_id("artist", "​") == ""


def test_wrong_arity_and_unknown_kind_are_errors():
    with pytest.raises(ValueError):
        generate_soul_id("album", "only one part")
    with pytest.raises(ValueError):
        generate_soul_id("bogus", "x")
