"""Guard against char-level title false positives in track matching.

Issue #769: playlist sync matched tracks that aren't in the library to a
DIFFERENT song by the SAME artist, with high confidence — e.g. "Dani
California" -> "Californication" (Red Hot Chili Peppers), "Under The Bridge"
-> "Around the World". The confidence formula is ``0.5*title + 0.5*artist``,
and a same-artist comparison always yields ``artist = 1.0``, so the title score
is the only thing that can tell two of an artist's songs apart. But the title
score is a ``difflib.SequenceMatcher`` character ratio, which over-credits
unrelated titles that happen to share a long substring ("californi…") or only a
stopword ("the"): 0.67 and 0.62 respectively. With the flat 0.5 artist term
that lands at 0.83 / 0.81 — well over the 0.7 sync threshold.

``titles_plausibly_same`` adds a cheap word-level sanity check on top of the
char ratio: accept a pair only when it's near-identical char-wise (so typos and
punctuation/casing variants — "Beleive"/"Believe", "HUMBLE."/"Humble" — still
match) OR the two titles share at least one significant (non-stopword) token.
Two genuinely different songs by the same artist share no content word, so they
get rejected; the real track is then correctly reported missing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")

# Articles / prepositions / conjunctions only. Deliberately NOT pronouns
# ("you", "me", "i") — those carry meaning in song titles and dropping them
# could strip the only shared word from a real match. "the" MUST stay here:
# without it "Under The Bridge" and "Around the World" would falsely share it.
_TITLE_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "to", "in", "on",
    "for", "with", "at", "by", "from",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fold(text: str) -> str:
    """Casefold and drop combining accents, so 'Versión' tokenises to 'version'.

    ``_TOKEN_RE`` is ASCII by design — a CJK tail yields no tokens at all, which
    is what makes the version rules ABSTAIN on scripts whose vocabulary they
    don't know instead of guessing. But that also silently excluded accented
    Romance forms: 'Versión 1988' and 'En Directo' are the exact Spanish cases
    :data:`_VERSION_MARKER_TOKENS` lists, and they tokenised to ``['versi', 'n']``.
    NFKD + dropping the combining marks brings them back without widening the
    rule to any new script.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", (text or "").casefold())
        if not unicodedata.combining(ch)
    )

# Char ratio at/above which two titles are treated as the same regardless of
# shared words — covers typos, punctuation, casing, accents. Tuned so single-
# word typos ("Beleive"/"Believe" = 0.857) pass while the #769 false positives
# ("Dani California"/"Californication" = 0.667) do not.
_NEAR_IDENTICAL = 0.85


def _content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(_fold(text)) if t not in _TITLE_STOPWORDS}


def titles_plausibly_same(
    title_a: str,
    title_b: str,
    char_similarity: float,
    *,
    near_identical: float = _NEAR_IDENTICAL,
) -> bool:
    """Whether two titles could be the same track, given their char similarity.

    ``title_a`` / ``title_b`` should already be normalised/cleaned (lowercased,
    brackets stripped) the same way the caller computed ``char_similarity``.

    Returns ``True`` when the pair is near-identical char-wise OR shares at
    least one significant (non-stopword) token. Returns ``False`` for two
    titles that are only moderately char-similar and share no content word —
    i.e. different songs the char ratio over-credited (#769)."""
    if char_similarity >= near_identical:
        return True
    ta = _content_tokens(title_a)
    tb = _content_tokens(title_b)
    # Word-overlap is only a reliable "different song" signal when at least one
    # side has 2+ content words — that's the #769 case where the char ratio
    # over-credits a shared substring ("Dani California"/"Californication") or
    # a stopword ("Under The Bridge"/"Around the World"). For single-word
    # titles there's no other word to share, so applying it would wrongly fail
    # legitimate stylized spellings ("Grey"/"Gray", "Tonite"/"Tonight",
    # "Thru"/"Through") that the char ratio rightly accepts. In that case defer
    # to the caller's existing char-similarity floor instead of force-failing.
    if max(len(ta), len(tb)) < 2 or not ta or not tb:
        return True
    return not ta.isdisjoint(tb)


_QUALIFIER_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def strip_redundant_context_qualifiers(title: str, *context_texts: str) -> str:
    """Remove parenthetical/bracket qualifiers that merely restate known context.

    A qualifier whose text appears (word-bounded) in one of ``context_texts``
    — typically the release's album title, or the other side of a comparison —
    is album context, not a version difference. #808: the wishlist held
    'Champagne Supernova (OurVinyl Sessions)' while the library track was the
    bare 'Champagne Supernova' on the album '… (OurVinyl Sessions)'; the
    qualifier restated the album, but the length-ratio penalty treated the
    pair as different songs and the cleanup never recognised the owned
    edition. Version markers that do NOT appear in any context ('(Live)',
    '(Remix)' on a studio album) are kept, so their mismatch penalty stands.
    """
    if not title:
        return title

    contexts = [c.casefold() for c in context_texts if c]
    if not contexts:
        return title

    def _drop(match: re.Match) -> str:
        inner = match.group(1).strip().casefold()
        if not inner:
            return " "
        pattern = r"\b" + re.escape(inner) + r"\b"
        for ctx in contexts:
            if re.search(pattern, ctx):
                return " "
        return match.group(0)

    out = _QUALIFIER_RE.sub(_drop, title)
    return re.sub(r"\s+", " ", out).strip()


# Qualifier tokens that mark a genuinely DIFFERENT recording/cut — these must
# keep blocking a match. Union of the matching-engine keyword lists plus the
# Spanish markers seen in real libraries (#825: 'En Directo…', 'Versión 1988',
# 'Dueto 2007'). Titles reaching the matcher are unidecode-normalized, so the
# ASCII forms ('version') cover the accented ones ('versión').
_VERSION_MARKER_TOKENS = frozenset({
    # English
    "remix", "mix", "rmx", "live", "acoustic", "unplugged", "instrumental",
    "karaoke", "demo", "demos", "edit", "version", "versions", "remaster",
    "remastered", "slowed", "reverb", "sped", "spedup", "speedup", "extended",
    "club", "mashup", "bootleg", "cover", "covers", "reprise", "session",
    "sessions", "mono", "stereo", "duet", "rework", "dub", "vip", "single",
    "radio", "alt", "alternate", "alternative", "take", "edition", "orchestral",
    "symphonic", "piano", "acapella", "cappella", "nightcore", "vocal",
    "clean", "explicit",
    # Distinct-track qualifiers — '(Interlude)' etc. are SEPARATE short tracks
    # that share the base name with the full song; never treat as subtitles.
    "interlude", "intro", "outro", "skit", "freestyle", "medley", "snippet",
    # Part/volume markers whose number can be non-numeric ('Pt. II') — the
    # digit guard below only catches actual digits.
    "pt", "part", "vol", "ii", "iii", "iv", "vi", "vii", "viii",
    # Romance languages (accent-folded by `_fold`, so 'versión' → 'version'
    # above covers it). Their word order puts the marker FIRST and the modifier
    # after it ('Versión Extendida', 'Version Française'), which no positional
    # rule can generalise without re-admitting 'Radio Ga Ga' — so the modifiers
    # are vocabulary too, listed under _VERSION_TAIL_FILLERS.
    "directo", "vivo", "dueto", "extendida", "extendido",
    "versione", "versao", "fassung",
    # Performance/edition markers only the dash-tail rule needs; harmless in
    # strip_subtitle_qualifiers, where one more marker only means one more
    # qualifier is KEPT (the conservative direction).
    "recorded", "bonus", "broadcast",
    # Japanese / K-pop catalogue abbreviations. Measured against the 13,728
    # real titles in the user's library, where the JP-language releases write
    # their version tags in ASCII: 'Inst Ver.', 'Movie ver.', 'Chill Ver.',
    # 'TV Size'. None of these carried a marker before, so every one of them
    # normalized differently from its '(…)' twin.
    "ver", "inst", "size",
    # Release-format suffixes the Deezer/iTunes catalogue appends to ALBUM
    # names ('Beyoncé (Platinum Edition) - EP'). 'ep' is safe as a last-token
    # rule even though anime rows use it for episodes: 'Lord of the Mysteries
    # EP 13' ends in the number, so it is not a tail.
    "ep", "remixes", "mixes",
})

# Markers that name a DIFFERENT track sharing the base name, not an annotated
# version of the same recording. The two consumers need opposite handling:
# strip_subtitle_qualifiers keeps a qualifier containing them (so the mismatch
# penalty stands), while the dash-tail rule must never DROP them — collapsing
# 'Song - Pt. 1' and 'Song - Pt. 2' (or 'Song' and 'Song - Interlude') onto one
# normalized identity scores a wrong file at 1.00 and turns a FAIL into a PASS.
_DISTINCT_TRACK_TOKENS = frozenset({
    "interlude", "intro", "outro", "skit", "medley", "snippet", "freestyle",
    "pt", "part", "vol", "ii", "iii", "iv", "vi", "vii", "viii",
})

# The vocabulary the dash-tail rule may act on.
_VERSION_TAIL_MARKERS = _VERSION_MARKER_TOKENS - _DISTINCT_TRACK_TOKENS

# Markers that legitimately introduce a venue ('Live at Wembley'). Deliberately
# a small subset: with the full marker set, 'Take On Me' and 'Piano in the Dark'
# would read as venue tails.
_PERFORMANCE_MARKERS = frozenset({
    "live", "recorded", "unplugged", "acoustic", "session", "sessions",
    "directo", "vivo",
})
_VENUE_PREPOSITIONS = frozenset({
    "at", "from", "in", "on",          # en
    "en", "desde", "em", "ao",         # es / pt
    "aus", "im",                       # de
})

# Padding that appears inside a real version tail without carrying identity of
# its own. Kept tight ON PURPOSE — never pronouns or nouns that could be the
# point of a title ('me', 'you', 'man', 'girl'), since every word added here
# widens rule B.
_VERSION_TAIL_FILLERS = frozenset({
    "the", "a", "an", "and", "or", "of", "with", "de", "la",
    "original", "official", "super", "ultra", "full", "new", "digital",
    "track", "album", "studio", "master", "anniversary", "deluxe", "special",
    "limited", "expanded", "reissue", "up",
    # Language qualifiers: they say which version, never which song.
    "francaise", "francais", "italiana", "italiano", "espanola", "espanol",
    "deutsche", "english", "japanese", "korean",
})
_TAIL_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# CJK version tags, matched against the WHOLE tail by equality.
#
# `_TOKEN_RE` is ASCII, so a Japanese/Chinese/Korean tail yields no tokens and
# the token rules abstain — which is the safe default for a script whose
# vocabulary they don't know, but it also left a real '- ライブ' tail
# normalizing differently from its '(Live)' twin. Equality (not substring) is
# what keeps a song actually called 「ライブが終わって」 intact.
_CJK_VERSION_TAGS = frozenset({
    # Japanese
    "ライブ", "ライヴ", "インスト", "インストゥルメンタル", "カラオケ",
    "オフボーカル", "リミックス", "アコースティック", "バージョン", "ヴァージョン",
    "生演奏", "伴奏", "短縮版", "劇場版",
    # Chinese
    "現場", "现场", "純音樂", "纯音乐", "伴奏版", "live版",
    # Korean
    "라이브", "인스트", "리믹스", "노래방", "반주",
})
_CJK_TRIM_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _is_tail_padding(token: str) -> bool:
    return token in _VERSION_TAIL_FILLERS or bool(_TAIL_YEAR_RE.match(token))


def is_trailing_version_qualifier(text: str) -> bool:
    """Whether a DASH-separated tail describes a version rather than a title.

    Brackets are unambiguous — anything inside ``(...)`` is an annotation, so
    a single marker token anywhere in the group is enough there. A dash is
    not: it separates artist from title, and plenty of real titles open with
    a marker word. "Contains a marker" therefore over-strips catastrophically
    (PR #1121 review — ``Queen - Radio Ga Ga`` → ``queen``, ``Billy Joel -
    Piano Man`` → ``billy joel``), and those decisions feed AcoustID
    verification, so it mis-verifies genuine tracks.

    What separates the families is WHERE the marker sits, but "ends in a
    marker" alone is too narrow to be the whole rule: Spotify's two most
    common tails, ``- Remastered 2011`` and ``- Live at Wembley Stadium``,
    both end in a non-marker word, so that rule left the dash form
    normalizing differently from the ``(Remastered 2011)`` bracket form — the
    exact drift this helper exists to prevent. Three shapes are accepted:

      A. the tail ENDS in a marker — ``Don Diablo Edit``, ``2011 Remaster``,
         ``Slowed + Reverb``, ``Live``. This is what admits the
         producer-credit forms; requiring the tail to be *entirely* marker
         words would drop them.
      B. markers plus padding ONLY — ``Remastered 2011``, ``Remaster 2009``,
         ``Sped Up``, ``Bonus Track``. Padding is a deliberately small closed
         set (:data:`_VERSION_TAIL_FILLERS`) plus 19xx/20xx years; arbitrary
         numbers are excluded so ``Take 3`` and ``Vol. 2`` stay put.
      C. a performance marker introducing a venue — ``Live at Wembley``,
         ``Live From Paris``, ``Recorded at Abbey Road``.

    and one veto that outranks all three: a tail containing a
    :data:`_DISTINCT_TRACK_TOKENS` word (``Pt. 2``, ``Interlude``) names a
    different track, so it is never dropped.

    Rule C's only known over-match is a real title of the shape ``Live in the
    Moment``. That is survivable by construction, not by luck:
    ``audio_verification.similarity`` scores the un-stripped form as well and
    keeps the better of the two, so a wrong strip costs nothing while a missed
    strip would leave a genuine ``- Remastered 2011`` track below threshold.

    ``_VERSION_MARKER_TOKENS`` stays shared with
    :func:`strip_subtitle_qualifiers` so both paths grow the same vocabulary.
    """
    tokens = _TOKEN_RE.findall(_fold(text))
    if not tokens:
        # No ASCII token at all — the only thing that can be decided here is
        # whether the whole tail IS one of the known CJK version words.
        return _CJK_TRIM_RE.sub("", (text or "").casefold()) in _CJK_VERSION_TAGS
    if any(t in _DISTINCT_TRACK_TOKENS for t in tokens):
        return False
    if tokens[-1] in _VERSION_TAIL_MARKERS:
        return True
    if any(t in _VERSION_TAIL_MARKERS for t in tokens) and all(
        t in _VERSION_TAIL_MARKERS or _is_tail_padding(t) for t in tokens
    ):
        return True
    for idx, token in enumerate(tokens):
        if token not in _VENUE_PREPOSITIONS:
            continue
        # Everything before the preposition must be version vocabulary, and at
        # least one word of it a performance marker ('En Directo en Madrid'
        # opens with a preposition of its own, hence they count as head
        # padding too). A failing head is not a verdict — a later preposition
        # may still open the venue ('Take On Me' finds none and stays put).
        head = tokens[:idx]
        if any(t in _PERFORMANCE_MARKERS for t in head) and all(
            t in _VERSION_TAIL_MARKERS or t in _VENUE_PREPOSITIONS or _is_tail_padding(t)
            for t in head
        ):
            return True
    return False


def strip_subtitle_qualifiers(title: str, other_title: str) -> str:
    """Remove bracketed qualifiers that are SUBTITLES, not version markers.

    #825 (carlosjfcasero): the wishlist held 'Llamando a la tierra (Serenade
    From the Stars)' — the song's official subtitle — while the library track
    was the bare 'Llamando a la tierra'. The qualifier appears in no album or
    counterpart title, so :func:`strip_redundant_context_qualifiers` keeps it,
    and the length-ratio penalty then crushes an obviously-same song to ~0.14.
    The sync matcher reported it missing on every run (re-adding it to the
    wishlist) and the cleanup — same matcher — could never remove it.

    A qualifier is stripped only when ALL of:
      * its text does not appear in ``other_title`` (if it does, the direct
        comparison already handles it);
      * it contains no version-marker token ('(Live)', '(Versión 1988)',
        '(Dueto 2007)' keep blocking — they are different recordings);
      * it introduces no digit token absent from ``other_title`` ('(Pt. 2)',
        '(2007)' are different releases, never subtitles).

    Inputs should be normalized the same way the caller compares them
    (lowercased / unidecode'd), like strip_redundant_context_qualifiers.
    """
    if not title:
        return title

    other = (other_title or "").casefold()
    other_tokens = set(_TOKEN_RE.findall(other))

    def _drop(match: re.Match) -> str:
        inner = match.group(1).strip().casefold()
        if not inner:
            return " "
        # Restated in the counterpart title — leave for the direct comparison.
        if re.search(r"\b" + re.escape(inner) + r"\b", other):
            return match.group(0)
        tokens = _TOKEN_RE.findall(inner)
        if any(t in _VERSION_MARKER_TOKENS for t in tokens):
            return match.group(0)
        if any(any(c.isdigit() for c in t) and t not in other_tokens for t in tokens):
            return match.group(0)
        return " "

    out = _QUALIFIER_RE.sub(_drop, title)
    return re.sub(r"\s+", " ", out).strip()


def numeric_tokens_differ(title_a: str, title_b: str) -> bool:
    """True when the digit-bearing tokens of two titles differ — 'Vol.4' vs
    'Vol.4.5', 'Album' vs 'Album 2'. A numeric difference is a different
    release (volume / part / sequel), never a '(Deluxe)'-style suffix:
    string similarity ('Vol.4' vs 'Vol.4.5' = 0.97) and token-subset checks
    both wave these through, which hung volume 4.5's cover art on volume 4
    (Sokhi). Shared digits on both sides ('1989' vs '1989 (Deluxe)') are
    fine.

    Tokenises on non-word runs but KEEPS word characters of every script, so a
    digit glued to a non-latin word stays its own digit-bearing token. Stripping
    to [a-z0-9] turned CJK into spaces, collapsing 'サウンドトラック2' to a bare
    '2' that a shared number elsewhere ('第2期' = season 2) already covered — so
    'Soundtrack' and 'Soundtrack2' both reduced to {'2'} and matched, hanging the
    wrong cover (Sokhi again)."""
    def _digit_tokens(text: str) -> frozenset:
        # \W is Unicode-aware for str: CJK/kana count as word chars, so a digit
        # stays attached to its word instead of collapsing to a bare '2'.
        tokens = re.sub(r"\W+", " ", (text or "").casefold()).split()
        return frozenset(t for t in tokens if any(c.isdigit() for c in t))

    return _digit_tokens(title_a) != _digit_tokens(title_b)


def base_title_before_dash(title: str) -> str:
    """The base title before Spotify's ' - <qualifier>' version separator.

    Spotify renders versions as 'Calma - Remix' / 'Song - Radio Edit' /
    'Track - Remastered 2019'. Libraries (and the files people actually have)
    very often store just the base — 'Calma' — so a literal search for
    'Calma - Remix' finds nothing and the OR-fuzzy fallback then floods on the
    common qualifier word ('remix' matches every remix). This returns the base
    ('Calma') for a base-title search fallback. Splits on the FIRST ' - ' (the
    spaced hyphen is Spotify's separator; a bare hyphen inside a word is left
    alone). Returns the title unchanged when there's no separator."""
    if not title:
        return title
    idx = title.find(' - ')
    return title[:idx].strip() if idx > 0 else title


def choose_best_title_candidate(
    search_norm: str,
    search_clean: str,
    candidates: Iterable[tuple[str, str, T]],
    similarity_fn: Callable[[str, str], float],
    *,
    threshold: float = 0.7,
) -> T | None:
    """Pick the best title candidate instead of the first acceptable one.

    Several UI/library paths strip parenthetical qualifiers for fallback matching,
    so both ``Ratata`` and ``Ratata (Afro Bros Remix)`` clean to ``ratata``.
    A first-match loop can therefore select the remix for a bare-title request
    when DB order happens to put the remix first. Rank exact normalized title
    matches before cleaned-title fallbacks, then fuzzy matches.
    """
    best_payload: T | None = None
    best_rank: tuple[float, float, float] | None = None

    for db_norm, db_clean, payload in candidates:
        if search_norm == db_norm:
            rank = (0, 0.0, abs(len(search_norm) - len(db_norm)))
        elif search_clean == db_clean:
            rank = (1, 0.0, abs(len(search_norm) - len(db_norm)))
        else:
            sim = max(similarity_fn(search_norm, db_norm), similarity_fn(search_clean, db_clean))
            if sim < threshold:
                continue
            rank = (2, -sim, abs(len(search_norm) - len(db_norm)))

        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_payload = payload

    return best_payload


__all__ = [
    "titles_plausibly_same",
    "is_trailing_version_qualifier",
    "strip_redundant_context_qualifiers",
    "strip_subtitle_qualifiers",
    "numeric_tokens_differ",
    "base_title_before_dash",
    "choose_best_title_candidate",
]
