"""Shared audio-verification decision core (pure; no file/DB I/O).

Single source of truth for normalization + the PASS/SKIP/FAIL decision used by
BOTH import-time verification (``core/acoustid_verification.py``) and the library
scan (``core/repair_jobs/acoustid_scanner.py``). Historically each path had its
own ``_normalize`` and decision branches that drifted apart and produced
inconsistent results (a correct cross-script anime-OST track passed at import but
was false-flagged by the scan). Centralising the decision here means the
thresholds, normalization, alias-aware comparison, cross-script handling, version
gate and duration guard are defined exactly once.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, List, Optional

from core.text.title_match import is_trailing_version_qualifier
from utils.logging_config import get_logger

logger = get_logger("audio_verification")

# Thresholds — the single definition both paths share.
MIN_ACOUSTID_SCORE = 0.80       # Minimum fingerprint score to trust a match.
TITLE_MATCH_THRESHOLD = 0.70    # Title similarity to consider a match.
ARTIST_MATCH_THRESHOLD = 0.60   # Artist similarity to consider a match.
CLEAR_MISMATCH_THRESHOLD = 0.30  # Below this artist sim = clear wrong song.
# Spotify's version separator is a SPACED dash (' - Remastered 2011'), so the
# rule needs whitespace next to the dash — with `\s*` a bare intra-word hyphen
# matched and 'Post-Remix' normalized to 'post'. ONE side is enough, and has to
# be: real catalogue rows write ']- Single' ('Cold Water … [Anirudh Diwali
# Edition]- Single'), where the bracket strip has already eaten the space in
# front. En/em dashes appear in the same role in provider metadata.
# The dash class covers what real metadata actually writes: ASCII hyphen, the
# unicode hyphen/en/em dashes, the minus sign, and the FULLWIDTH hyphen-minus a
# Japanese tagger produces (残酷な天使のテーゼ － Instrumental).
_DASH_CHARS = r"\-‐‑‒–—―−－"
_DASH_QUALIFIER_RE = re.compile(
    rf"(?:\s[{_DASH_CHARS}]\s*|\s*[{_DASH_CHARS}]\s)(?P<qualifier>[^{_DASH_CHARS}]+)$"
)


class Decision(Enum):
    PASS = "pass"
    SKIP = "skip"
    FAIL = "fail"


@dataclass
class Outcome:
    decision: Decision
    title_sim: float = 0.0
    artist_sim: float = 0.0
    matched_title: str = ""
    matched_artist: str = ""
    reason: str = ""


def _finish_normalization(s: str) -> str:
    s = re.sub(r'\s*-\s*from\s+.+$', '', s, flags=re.IGNORECASE)
    # Path/separator punctuation -> space so a title keeps matching a source
    # filename that substituted '_' for an illegal '/' or ':' (#851): the on-disk
    # "You See Big Girl _ T_T" must normalize the same as "You See Big Girl / T:T".
    # Done before the strip below so they become word boundaries, not joins.
    s = re.sub(r'[\\/:_]+', ' ', s)
    # Drop remaining punctuation but keep word chars (incl. CJK) + spaces.
    s = re.sub(r'[^\w\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _normalized_readings(text: str) -> tuple:
    """``(canonical, verbatim)`` normalized forms of ``text``.

    ``verbatim`` is ``None`` unless a ``' - <qualifier>'`` version tail was
    actually dropped, in which case it is the same normalization with the tail
    kept. No token rule can tell ``Taylor Swift - Long Live`` (artist + title)
    from ``Halo - Long Live`` (title + version tag) — both end in a version
    marker — so :func:`similarity` scores both readings and keeps the better
    one. That is what stops the strip from being load-bearing: a wrong strip
    costs a few points instead of collapsing a real title to the artist name
    and quarantining a correct file.
    """
    if not text:
        return "", None
    s = text.lower().strip()
    # Annotations that are metadata, not core identity.
    s = re.sub(r'\s*\([^)]*\)', '', s)
    s = re.sub(r'\s*\[[^\]]*\]', '', s)
    s = re.sub(r'\s*<[^>]*>', '', s)
    # Trailing featuring / version tags.
    s = re.sub(r'\s+(?:feat\.?|ft\.?|featuring)\s+.*$', '', s, flags=re.IGNORECASE)
    dash_qualifier = _DASH_QUALIFIER_RE.search(s)
    if dash_qualifier and is_trailing_version_qualifier(dash_qualifier.group("qualifier")):
        return (
            _finish_normalization(s[:dash_qualifier.start()].rstrip()),
            _finish_normalization(s),
        )
    return _finish_normalization(s), None


def normalize(text: str, *, strip_version_tail: bool = True) -> str:
    """Normalize a title/artist for comparison.

    lowercase; strip ``()`` / ``[]`` / ``<>`` annotations (version tags,
    performer credits like ``<Vocal: MIKA KOBAYASHI>``); strip trailing
    version / featuring tags; KEEP CJK characters (``\\w`` is unicode-aware) so
    Japanese/Chinese/Korean titles produce a comparable form instead of an empty
    string; collapse whitespace.

    ``strip_version_tail=False`` keeps a ``' - <qualifier>'`` tail — the second
    reading :func:`similarity` scores, see :func:`_normalized_readings`.
    """
    canonical, verbatim = _normalized_readings(text)
    if not strip_version_tail and verbatim is not None:
        return verbatim
    return canonical


def similarity(a: str, b: str) -> float:
    """Similarity (0.0–1.0) between two strings after normalization.

    Scored across both readings of each side (see :func:`_normalized_readings`),
    best pairing wins. Only a stripped dash tail produces a second reading, so
    the common path is the single comparison it has always been.
    """
    va = [v for v in _normalized_readings(a) if v]
    vb = [v for v in _normalized_readings(b) if v]
    if not va or not vb:
        return 0.0
    best = 0.0
    for na in va:
        for nb in vb:
            if na == nb:
                return 1.0
            best = max(best, SequenceMatcher(None, na, nb).ratio())
    return best


_match_engine = None


def _detect_title_version(title: str) -> str:
    """Version label ('original'/'instrumental'/'live'/'remix'/...) for a title."""
    global _match_engine
    if not title:
        return 'original'
    if _match_engine is None:
        from core.matching_engine import MusicMatchingEngine
        _match_engine = MusicMatchingEngine()
    version_type, _ = _match_engine.detect_version_type(title)
    return version_type


def _alias_aware_artist_sim(expected_artist: str, actual_artist: str,
                            aliases: Optional[Any] = None) -> float:
    """Best artist similarity across (expected, *aliases) vs actual.

    Bridges cross-script artist comparisons (kanji↔romaji etc) when MusicBrainz
    aliases are available. ``aliases`` is an iterable of alias strings, or a
    callable resolving them lazily (only invoked when direct similarity falls
    below threshold — keeps the happy path lookup-free).
    """
    from core.matching.artist_aliases import artist_names_match

    direct = similarity(expected_artist, actual_artist)
    if aliases is None:
        return direct
    if direct >= ARTIST_MATCH_THRESHOLD:
        return direct
    resolved = aliases() if callable(aliases) else aliases
    if not resolved:
        return direct
    _matched, score = artist_names_match(
        expected_artist, actual_artist, aliases=resolved,
        threshold=ARTIST_MATCH_THRESHOLD, similarity=similarity,
    )
    # Diagnostic: an alias rescued a comparison direct similarity would have
    # failed. INFO since it's a user-visible decision (PASS instead of FAIL).
    if score >= ARTIST_MATCH_THRESHOLD and direct < ARTIST_MATCH_THRESHOLD:
        from core.matching.artist_aliases import best_alias_match
        winner, _ = best_alias_match(
            expected_artist, actual_artist, resolved, similarity=similarity,
        )
        logger.info(
            "Artist alias rescued comparison: expected=%r vs actual=%r "
            "(direct sim=%.2f, alias %r → score=%.2f)",
            expected_artist, actual_artist, direct, winner, score,
        )
    return score


def _find_best_title_artist_match(recordings, expected_title, expected_artist,
                                  aliases=None):
    """Return (best_recording, title_sim, artist_sim) — title weighted higher.

    Ties are broken in favour of a candidate whose VERSION matches the expected
    one. `normalize` deliberately strips bracketed version tags, so "Celebrity"
    and "Celebrity (karaoke)" both score title_sim 1.0 against an expected
    "Celebrity" — an exact tie. With a strict `>` the winner was simply whichever
    MusicBrainz happened to list first, and `evaluate`'s version gate then failed
    on it and reported "Wrong download: 'Celebrity' is actually 'Celebrity
    (karaoke)'" — for a file that is perfectly correct, with the matching
    recording sitting in the same candidate list (#1132).

    Reversing the candidate order flipped the verdict between FAIL and PASS,
    which is what makes this a bug rather than a judgement call.
    """
    expected_version = _detect_title_version(expected_title or '')
    best_rec = None
    best_title_sim = 0.0
    best_artist_sim = 0.0
    best_key = None
    for rec in recordings:
        title = rec.get('title') or ''
        artist = rec.get('artist') or ''
        title_sim = similarity(expected_title, title)
        artist_sim = _alias_aware_artist_sim(expected_artist, artist, aliases)
        combined = (title_sim * 0.6) + (artist_sim * 0.4)
        # Similarity dominates; version agreement only settles a draw.
        key = (combined, 1 if _detect_title_version(title) == expected_version else 0)
        if best_key is None or key > best_key:
            best_key = key
            best_rec = rec
            best_title_sim = title_sim
            best_artist_sim = artist_sim
    return best_rec, best_title_sim, best_artist_sim


def fingerprint_is_ambiguous(recordings: List[dict]) -> bool:
    """True when the fingerprint's best-scoring recordings name DIFFERENT songs.

    AcoustID returns results, and each result carries a whole LIST of MusicBrainz
    recordings that all share that result's single score (see
    ``acoustid.parse_lookup_result``). So "the top match" is frequently a tie
    between several recordings, and their order is just MusicBrainz's — not a
    ranking. Mature AcoustID entries accumulate mis-submitted links, so that tie
    can span genuinely different songs by the same artist.

    When that happens the fingerprint honestly cannot say which track this is,
    and any single "it's actually X" claim is a coin flip. #1132: a file of
    Chicago's "You're the Inspiration" was reported as "Saturday in the Park",
    and the reporter found "almost all suggestions are wrong" — different songs,
    or instrumental/karaoke/acoustic variants of the right one.

    Deciding whether the file is MISLABELLED is still sound (that asks whether
    ANY candidate matches, which ties don't affect). Only the claim about what
    the file actually IS has to be withheld.
    """
    if not recordings:
        return False
    scored = [r for r in recordings if r.get('score') is not None]
    if not scored:
        # No per-recording scores to compare — treat >1 distinct title as
        # ambiguous, since nothing distinguishes them.
        titles = {_recording_identity(r.get('title')) for r in recordings}
        titles.discard('')
        return len(titles) > 1
    top = max(r['score'] for r in scored)
    top_titles = {
        _recording_identity(r.get('title'))
        for r in scored if r['score'] >= top
    }
    top_titles.discard('')
    return len(top_titles) > 1


def _recording_identity(title: Optional[str]) -> str:
    """Case/punctuation-insensitive key for "is this the same RECORDING".

    Deliberately NOT `normalize`: that strips bracketed version tags (so
    "Song" and "Song (Instrumental)" collapse to one string — which is what
    makes the separate version gate necessary). Here an instrumental IS a
    different recording, and reporting one in place of the other is exactly
    what #1132 complained about, so the qualifier has to survive.
    """
    if not title:
        return ""
    s = str(title).lower().strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def evaluate(expected_title: str, expected_artist: str,
             recordings: List[dict], *, fingerprint_score: float,
             aliases_provider: Optional[Any] = None,
             accept_version: Optional[str] = None) -> Outcome:
    """Decide PASS / SKIP / FAIL for a fingerprinted file against expected
    title/artist. Pure: no I/O. Shared by import verification and library scan.

    ``aliases_provider``: iterable or callable of expected-artist aliases
    (kanji/cyrillic/etc) used to bridge cross-script comparisons.

    ``accept_version``: a version label the caller went and fetched ON PURPOSE
    (Settings → prefer a version), so the version gate must not call it a wrong
    song. The source title says "Song" and the file really is "Song (Extended
    Mix)" — that difference is the point, not a fault. Only the version gate
    loosens; title and artist still have to agree, so a genuinely wrong song
    fails exactly as before. None (the default, and the library scan always)
    leaves the gate strict.

    Note: fingerprint-collision duration checks are the caller's responsibility
    (the library scan pre-checks the top recording's length before calling this)
    so the decision here stays purely about title/artist/version identity.
    """
    from core.matching.script_compat import is_cross_script_mismatch
    from core.matching.version_mismatch import is_acceptable_version_mismatch

    # No expected artist on record (legacy/compilation rows): compare on title
    # only — the old scanner treated this as artist-match=1.0 and a missing DB
    # value is no evidence the file is wrong.
    no_expected_artist = not normalize(expected_artist or '')

    best_rec, title_sim, artist_sim = _find_best_title_artist_match(
        recordings, expected_title, expected_artist, aliases_provider,
    )
    if no_expected_artist:
        artist_sim = 1.0
    if not best_rec:
        return Outcome(Decision.SKIP, reason="No recordings with title/artist info")

    matched_title = best_rec.get('title', '?') or '?'
    matched_artist = best_rec.get('artist', '?') or '?'

    # Is each dimension's score EVIDENCE at all? A similarity between two
    # different writing systems carries no information — romaji vs kanji scores
    # 0.00 whether or not it names the same thing — so such a dimension is
    # UNKNOWN, never FAILED, and must never be what a FAIL rests on. Two
    # production findings came from ignoring that: a 100% fingerprint with a
    # 100% title match was reported as "Wrong download" because
    # 'Sawano Hiroyuki' vs '澤野弘之' scored 0.00 and 0.00 is below
    # CLEAR_MISMATCH_THRESHOLD. Same-script disagreement stays real evidence,
    # so a genuinely wrong artist or song fails exactly as before.
    title_comparable = not is_cross_script_mismatch(expected_title, matched_title)
    artist_comparable = not is_cross_script_mismatch(expected_artist, matched_artist)

    def out(dec, reason):
        return Outcome(dec, title_sim, artist_sim, matched_title, matched_artist, reason)

    # Version gate: original vs instrumental/live/remix is a real difference.
    expected_version = _detect_title_version(expected_title)
    matched_version = _detect_title_version(matched_title)
    if expected_version != matched_version:
        # the caller asked for this exact version, so the file reporting it is
        # the setting working, not a mismatch.
        _asked_for_it = bool(accept_version) and matched_version == accept_version
        if not _asked_for_it and not is_acceptable_version_mismatch(
            expected_version, matched_version,
            fingerprint_score=fingerprint_score,
            title_similarity=title_sim, artist_similarity=artist_sim,
        ):
            return out(Decision.FAIL,
                       f"Version mismatch: expected ({expected_version}) "
                       f"but file is ({matched_version}) — matched "
                       f"'{matched_title}' by '{matched_artist}'")

    # Clean match.
    if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim >= ARTIST_MATCH_THRESHOLD:
        return out(Decision.PASS,
                   f"Audio verified: matches '{matched_title}' by '{matched_artist}' "
                   f"(title {title_sim:.0%}, artist {artist_sim:.0%})")

    # Title matches, artist doesn't — cover/collab vs genuinely wrong.
    if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim < ARTIST_MATCH_THRESHOLD:
        for rec in recordings:
            if _alias_aware_artist_sim(
                expected_artist, rec.get('artist', ''), aliases_provider,
            ) >= ARTIST_MATCH_THRESHOLD:
                return out(Decision.PASS,
                           f"Expected artist found in AcoustID results: "
                           f"'{rec.get('title', '?') or '?'}' by "
                           f"'{rec.get('artist', '?') or '?'}'")
        if not artist_comparable:
            # The title already agrees and the artist score is unreadable, so
            # there is nothing here that says the file is wrong.
            #
            # Deliberate and known: a cover of the right song by a non-Latin
            # artist is a real wrong download and is unreportable through this
            # branch. Nothing cheap distinguishes it — MusicBrainz alias lists
            # are incomplete often enough that "the aliases did not match" is
            # not evidence either, and a fingerprint bar here would re-quarantine
            # the correct files this branch exists for. A matching title after a
            # matching fingerprint is corroboration; we take it.
            return out(Decision.SKIP,
                       f"Title matches and the artist is written in a different "
                       f"script, so the names cannot be compared: "
                       f"'{matched_title}' by '{matched_artist}' "
                       f"(expected '{expected_artist}')")
        if artist_sim < CLEAR_MISMATCH_THRESHOLD:
            return out(Decision.FAIL,
                       f"Audio mismatch: '{matched_title}' by '{matched_artist}' "
                       f"— expected artist not found")
        return out(Decision.SKIP,
                   f"Title matches but artist ambiguous (cover/collab?): closest "
                   f"match '{matched_title}' by '{matched_artist}' "
                   f"(artist {artist_sim:.0%})")

    # Title doesn't match — scan all recordings for a version-matched hit.
    def _title_sim(a, b):
        return similarity(a, b)

    def _artist_sim(ea, aa):
        return _alias_aware_artist_sim(ea, aa, aliases_provider)

    candidate = None
    for rec in recordings:
        if _detect_title_version(rec.get('title') or '') != expected_version:
            continue
        if (similarity(expected_title, rec.get('title') or '') >= TITLE_MATCH_THRESHOLD
                and _alias_aware_artist_sim(
                    expected_artist, rec.get('artist', ''), aliases_provider,
                ) >= ARTIST_MATCH_THRESHOLD):
            candidate = rec
            break
    if candidate is not None:
        return out(Decision.PASS,
                   f"Scan match found in AcoustID results: "
                   f"'{candidate.get('title', '?') or '?'}' by "
                   f"'{candidate.get('artist', '?') or '?'}'")

    # High-confidence / cross-script skips (don't quarantine a correct file).
    has_non_ascii = (any(ord(c) > 127 for c in (expected_title or ''))
                     or any(ord(c) > 127 for c in matched_title))
    language_script_skip = (fingerprint_score >= 0.95 and has_non_ascii
                            and artist_sim >= ARTIST_MATCH_THRESHOLD)
    high_confidence_strong_match_skip = (fingerprint_score >= 0.95
                                         and title_sim >= 0.80
                                         and artist_sim >= ARTIST_MATCH_THRESHOLD)
    cross_script_artist_skip = (fingerprint_score >= MIN_ACOUSTID_SCORE
                                and artist_sim >= ARTIST_MATCH_THRESHOLD
                                and not artist_comparable)
    # The title is the dimension that disagreed — but if it is written in
    # another script its score was never readable, and the artist either agrees
    # or is unreadable too. Nothing comparable disagrees, so no claim can be
    # made. `language_script_skip` above covered a slice of this behind a
    # fingerprint >= 0.95 bar, which left an ordinary 0.90 match on a
    # Japanese-titled track quarantined; the script signal does not depend on
    # how well the fingerprint scored.
    #
    # Known and accepted: when BOTH dimensions are unreadable there is no
    # evidence in either direction, so a genuinely wrong non-Latin download is
    # unreportable here. Re-adding a fingerprint bar for that case was tried and
    # reverted — it does not separate the two, because the score says nothing
    # about the NAMES. What it does instead is quarantine the correct file this
    # branch exists for: "Zankoku na Tenshi no These" by "Yoko Takahashi" against
    # 残酷な天使のテーゼ by 高橋洋子 at a perfectly ordinary 0.90 is the same
    # shape as a wrong one, and that is the direction that costs a user their
    # file. Silence is the safe half of an unanswerable question.
    incomparable_title_skip = (
        not title_comparable
        and (not artist_comparable or artist_sim >= ARTIST_MATCH_THRESHOLD)
    )
    if (language_script_skip or high_confidence_strong_match_skip
            or cross_script_artist_skip or incomparable_title_skip):
        return out(Decision.SKIP,
                   f"Likely same song in different language/script: "
                   f"matched '{matched_title}' by '{matched_artist}' "
                   f"(fingerprint {fingerprint_score:.0%})")

    return out(Decision.FAIL,
               f"Audio mismatch: file identified as '{matched_title}' by "
               f"'{matched_artist}', expected '{expected_title}' by '{expected_artist}'")
