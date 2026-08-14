"""Normalize streaming/YouTube-style source track metadata for matching.

Issue #768: source playlists — especially ones seeded from YouTube — carry
video-style metadata: the title is ``"Artist - Song"`` and the artist is a
channel name like ``"Official Arctic Monkeys"``, ``"Arctic Monkeys - Topic"``,
or ``"ColdplayVEVO"``. The library/media-server side has the clean ``"Song"`` /
``"Arctic Monkeys"``. Both matching paths (the sync confidence scorer and the
playlist-editor reconcile) then fail to pair them — the track is reported
"not matched" / shows up as an orphan "extra" even though it exists.

These helpers strip that channel/video decoration so the cleaned source can be
compared against the clean library metadata. Pure, no I/O.

Conservative by construction:
- ``strip_artist_prefix`` removes a leading ``"<artist><sep>"`` only when the
  prefix EQUALS the artist we're matching against. So ``"Death - Pull the
  Plug"`` by ``"Death"`` is helped, while ``"Marvin Gaye"`` by Charlie Puth
  (title is not ``"Charlie Puth - ..."``) is left untouched, and a hyphenated
  word like ``"Self-Titled"`` is never split (a separator needs surrounding
  whitespace, or a colon).
- ``clean_source_artist`` only removes well-known channel decorations.

Both are intended to be applied as ADDITIONAL match candidates (best-of), so
an over-eager strip can only add a comparison, never remove the original.
"""

from __future__ import annotations

import re

from core.text.normalize import normalize_for_comparison

# Artist/title separator: a dash/pipe/tilde flanked by whitespace, OR a colon
# (with optional trailing space). Whitespace-flanking keeps "Self-Titled" and
# "Jay-Z" intact while still splitting "Artist - Title".
_SEP_SPLIT = re.compile(r"\s+[-–—|~]\s+|\s*:\s+")

# YouTube auto-generated artist channel: "Arctic Monkeys - Topic".
_TOPIC_SUFFIX = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
# "Official " / "The Official " channel prefix.
_OFFICIAL_PREFIX = re.compile(r"^\s*(?:the\s+)?official\s+", re.IGNORECASE)
# Trailing VEVO, attached ("ColdplayVEVO") or spaced ("Coldplay VEVO").
_VEVO_SUFFIX = re.compile(r"\s*vevo\s*$", re.IGNORECASE)

# Scripts that YouTube Music restates in Latin alongside the original title.
_NON_LATIN = re.compile(
    "["
    "぀-ヿ"              # hiragana + katakana
    "㐀-䶿一-鿿"  # CJK ideographs
    "가-힯"              # hangul
    "Ѐ-ӿ"              # cyrillic
    "Ͱ-Ͽ"              # greek
    "֐-׿"              # hebrew
    "؀-ۿ"              # arabic
    "฀-๿"              # thai
    "ऀ-ॿ"              # devanagari
    "]"
)

# Decoration around the *same* title: "(Album Mix)", "[HD]", "-Naruto Mix-".
_BRACKETED = re.compile(r"[（(\[【｢「].*?[)）\]】｣」]")
_DASHED_TAIL = re.compile(r"\s-[^-]+-\s*$")


def clean_source_artist(artist: str) -> str:
    """Strip well-known streaming-channel decoration from an artist name.

    ``"Official Arctic Monkeys"`` → ``"Arctic Monkeys"``;
    ``"Arctic Monkeys - Topic"`` → ``"Arctic Monkeys"``;
    ``"ColdplayVEVO"`` → ``"Coldplay"``. Returns the input unchanged when
    nothing matches, and never returns empty for non-empty input."""
    if not artist:
        return artist
    s = artist.strip()

    topic = _TOPIC_SUFFIX.sub("", s).strip()
    if topic and topic != s:
        s = topic

    official = _OFFICIAL_PREFIX.sub("", s).strip()
    if official:
        s = official

    # Only strip VEVO if at least 2 chars of name remain (don't empty "VEVO").
    vevo = _VEVO_SUFFIX.sub("", s).strip()
    if len(vevo) >= 2 and vevo != s:
        s = vevo

    return s or artist


def strip_artist_prefix(title: str, artist: str) -> str:
    """Remove a leading ``"<artist><separator>"`` from ``title`` when the prefix
    equals ``artist`` (accent/case-folded). Otherwise return ``title`` unchanged.

    ``("Arctic Monkeys - Do I Wanna Know?", "Arctic Monkeys")`` → ``"Do I Wanna
    Know?"``. Never returns an empty string."""
    if not title or not artist:
        return title
    na = normalize_for_comparison(artist)
    if not na:
        return title
    parts = _SEP_SPLIT.split(title, maxsplit=1)
    if len(parts) == 2:
        left, right = parts
        right = right.strip()
        if right and normalize_for_comparison(left) == na:
            return right
    return title


def _title_core(text: str) -> str:
    """The title with same-title decoration removed, normalized for comparison.

    ``"Rain (Long Ver.)"`` and ``"Rain (Long Version)"`` both reduce to ``"rain"``,
    which is what makes them recognizable as one title stated twice."""
    s = _BRACKETED.sub(" ", text or "")
    s = _DASHED_TAIL.sub(" ", s)
    return normalize_for_comparison(" ".join(s.split()))


def restated_title(title: str) -> str | None:
    """For a title that states the SAME track twice, return the Latin statement.

    YouTube Music serves localized titles with a transliteration appended:
    ``"狂乱 Hey Kids!! - Kyouran Hey Kids!!"``, ``"すずめ - Suzume (feat. Toaka)"``.
    Providers index the transliteration, so scoring the raw string against
    ``"Kyouran Hey Kids!!"`` finds nothing. The same shape appears within Latin
    text when a mix is renamed: ``"COLORS (Album Mix) - Colors (Ailu Mix)"``.

    Returns ``None`` unless the two halves are demonstrably the same title, by
    one of two independent tests:

    * **different scripts** — one half contains non-Latin script and the other
      does not, so they cannot be title-and-subtitle; the Latin half is returned.
    * **same core** — both halves reduce to the same string once bracketed and
      dash-delimited decoration is dropped.

    Everything else is left alone, so ``"Sketchy - Molly And The Zombies"``
    (title then band) and ``"Last Kiss (cover) - Brian Fallon"`` are untouched.
    Callers use the result as an ADDITIONAL candidate, never a replacement."""
    if not title:
        return None

    # A title can be stated more than twice — "烏 - Raven - Karasu - Raven"
    # carries the original, a translation and a transliteration. Splitting on
    # every separator and taking the first Latin-only statement handles those
    # as well as the plain two-part case.
    segments = [s.strip() for s in _SEP_SPLIT.split(title)]
    segments = [s for s in segments if s]
    if len(segments) < 2:
        return None

    latin = [s for s in segments if not _NON_LATIN.search(s)]
    if latin and len(latin) != len(segments):
        return latin[0] if latin[0] != title else None

    # All one script: only a demonstrable restatement counts — both halves
    # reduce to the same string once decoration is dropped.
    if len(segments) == 2:
        left_core, right_core = _title_core(segments[0]), _title_core(segments[1])
        if left_core and left_core == right_core:
            return segments[1]
    return None


def canonical_source_track(title: str, artist: str) -> tuple[str, str]:
    """Best-effort clean (title, artist) for matching a streaming/YouTube source
    against clean library metadata. Cleans the artist first, then strips a
    leading artist prefix from the title using EITHER the cleaned or the raw
    artist (YouTube titles prepend the real artist, not the channel name).

    Falls back to :func:`restated_title` when no artist prefix was found, so a
    title stated twice ("original - transliteration") contributes its Latin
    statement as the canonical candidate."""
    cleaned_artist = clean_source_artist(artist)
    new_title = strip_artist_prefix(title, cleaned_artist)
    if new_title == title and cleaned_artist != artist:
        new_title = strip_artist_prefix(title, artist)
    if new_title == title:
        new_title = restated_title(title) or title
    return new_title, cleaned_artist


__all__ = [
    "canonical_source_track",
    "clean_source_artist",
    "restated_title",
    "strip_artist_prefix",
]
