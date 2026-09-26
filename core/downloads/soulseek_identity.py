"""Target-aware interpretation of Soulseek paths and album tracklists.

The generic filename parser has to choose one title without knowing what was
requested. Soulseek paths are too varied for that choice to be authoritative;
keep a small set of plausible titles and compare them to the requested track.

Evidence is graded, never binary. An exact title with an agreeing track
number is the strongest; an exact title whose number disagrees is still the
same song on a differently-numbered edition (deluxe, reissue, regional); a
layout nothing here recognizes is left open for the confidence scorer rather
than rejected. Version detection ("Live", "Remix") stays with the matching
engine; this module only recognizes a version suffix when the user asked
for that version.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Sequence

from core.text.normalize import normalize_for_comparison
from core.text.track_prefix import parse_track_prefix


_YEAR = re.compile(r'(?<!\d)[\[(]?((?:19|20)\d{2})[\])]?')
_DISC_DIR = re.compile(r'^(?:disc|disk|cd)\s*[-._ ]*(\d{1,2})$', re.IGNORECASE)
_AUDIO_EXTENSION = re.compile(r'\.[a-z0-9]{2,5}$', re.IGNORECASE)
# slskd renames a colliding download "<name>_<ticks>"; scene rips append a
# short hex release hash. Neither is part of the title.
_FILENAME_SUFFIX = re.compile(r'(?:_\d{15,}|[-_][0-9a-f]{8})$', re.IGNORECASE)
_TECHNICAL_TAG = re.compile(r'\s*[\[(](?:flac|mp3|320kbps|v0|lossless|24bit|16bit|hi res)[\])]\s*', re.IGNORECASE)
_REMASTER_TAG = re.compile(r'\s*[\[(](?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?[\])]\s*', re.IGNORECASE)
_REMASTER_SUFFIX = re.compile(r'\s*[-–]\s*(?:(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)\s*$', re.IGNORECASE)
_FEAT_TAG = re.compile(r'\s*[\[(](?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]\s*', re.IGNORECASE)
_CLEAN_TAG = re.compile(r'\s*[\[(](?:explicit|clean)[\])]\s*', re.IGNORECASE)
# "(Original Mix)" is the plain recording on dance releases; "Acoustic
# Version" and "Acoustic" name the same one. Trailing unbracketed credits
# are credits, as the bracketed form above already is.
_ORIGINAL_MIX_TAG = re.compile(r'\s*(?:[\[(]|[-–]\s*|\s)original (?:mix|version)[\])]?\s*$', re.IGNORECASE)
_VERSION_WORD_TAG = re.compile(r'\b(?:version|ver\.?)\b', re.IGNORECASE)
_TRAILING_FEAT = re.compile(r'\s+(?:feat\.?|ft\.?|featuring)\s+.+$', re.IGNORECASE)
# A requested version ("Extended Mix") appended to the requested title.
_VERSION_WORD = re.compile(
    r'\b(?:live|remix|mix|acoustic|instrumental|extended|demo|karaoke|radio edit|single edit)\b'
)
# A weak edge in release assignment needs this much title similarity.
_WEAK_TITLE_SIMILARITY = 0.80


def normalize(text: Any) -> str:
    """Shared accent-aware comparison, retaining word boundaries.

    An apostrophe inside a word is dropped rather than split ("I'm" and
    "im" are the same word on Soulseek) and "&" reads as "and".
    """
    text = re.sub(r"(?<=\w)['’](?=\w)", '', str(text or '')).replace('&', ' and ')
    folded = normalize_for_comparison(text)
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', folded)).strip()


def _title_key(text: str) -> str:
    """Ignore mastering/credit decorations, never recording versions."""
    text = html.unescape(text)
    text = _REMASTER_TAG.sub(' ', text)
    text = _REMASTER_SUFFIX.sub(' ', text)
    text = _ORIGINAL_MIX_TAG.sub(' ', text)
    text = _FEAT_TAG.sub(' ', text)
    text = _TRAILING_FEAT.sub(' ', text)
    text = _CLEAN_TAG.sub(' ', text)
    text = _VERSION_WORD_TAG.sub(' ', text)
    return normalize(text)


def _without_year(text: str) -> str:
    return re.sub(r'\s+', ' ', _YEAR.sub(' ', text)).strip(' -_.')


@dataclass(frozen=True)
class TitleEvidence:
    title: str
    source: str
    number: int | None = None
    disc: int | None = None


@dataclass(frozen=True)
class IdentityResult:
    matches: bool
    reason: str
    title: str = ''
    source: str = ''
    number: int | None = None
    disc: int | None = None
    artist_path_evidence: bool = False
    album_path_evidence: bool = False
    # None when either side has no number/disc to compare.
    number_agrees: bool | None = None
    # Best fuzzy ratio between the requested title and any interpretation;
    # only meaningful when ``matches`` is False.
    title_similarity: float = 0.0


@lru_cache(maxsize=16384)
def title_interpretations(filename: str, artist: str = '', album: str = '') -> tuple[TitleEvidence, ...]:
    """Produce bounded interpretations, not a single guessed filename parse."""
    segments = [part.strip() for part in str(filename or '').replace('\\', '/').split('/') if part.strip()]
    if not segments:
        return ()
    stem = _FILENAME_SUFFIX.sub('', _AUDIO_EXTENSION.sub('', segments[-1])).strip()
    disc = None
    for segment in reversed(segments[:-1]):
        found = _DISC_DIR.fullmatch(segment)
        if found:
            disc = int(found.group(1))
            break
    cleaned_stem = _TECHNICAL_TAG.sub(' ', stem)
    prefix = parse_track_prefix(cleaned_stem)
    if prefix.disc is not None:
        disc = prefix.disc
    number = prefix.number
    variants: list[TitleEvidence] = []
    seen: set[str] = set()

    def add(text: str, source: str, parsed_number: int | None = number) -> None:
        key = _title_key(text)
        if key and key not in seen and len(variants) < 16:
            seen.add(key)
            variants.append(TitleEvidence(key, source, parsed_number, disc))

    base = prefix.remainder.strip(' -_.')
    normalized_artist = normalize(artist)
    # A number followed only by whitespace can be part of the actual title
    # ("7 rings"), so the unstripped stem stays a candidate.
    if re.match(r'^\s*\d+\s+[A-Za-z]', cleaned_stem):
        add(cleaned_stem, 'literal-leading-number', None)
    add(base, 'basename')
    if artist:
        # Scene-style names join words with underscores and drop the spaces
        # around the separator ("fountains_of_wayne-stacys_mom"), which the
        # whitespace-flanked separator in core.text.strip_artist_prefix is
        # built to leave alone.
        artist_words = [re.escape(word) for word in re.split(r'[\s_-]+', artist) if word]
        if artist_words:
            artist_prefix = r'^' + r'[\s_-]+'.join(artist_words)
            featured_prefix = re.compile(
                artist_prefix
                + r'\s+(?:feat\.?|ft\.?|featuring)\s+.+?\s+[-–:]\s+(.+)$',
                re.IGNORECASE,
            )
            plain_prefix = re.compile(
                artist_prefix + r'\s*[-–:_]\s*(.+)$', re.IGNORECASE,
            )
            # a collaborator list glued to the artist with a tight dash
            # ("01-J Balvin & Bad Bunny-MOJAITA", "A, B-Title", "A x B-Title")
            # is neither of the above: the featured form wants a spaced dash
            # and the plain form stops at the connector. every track on a
            # collab album scored 0 coverage without this
            collab_prefix = re.compile(
                artist_prefix
                + r'(?:\s*[&,;+]\s*|\s+(?:and|feat\.?|ft\.?|featuring|with|x|vs\.?)\s+)'
                + r'.+?\s*[-–:_]\s*(.+)$',
                re.IGNORECASE,
            )
            found_prefix = (featured_prefix.match(base) or plain_prefix.match(base)
                            or collab_prefix.match(base))
            if found_prefix:
                add(found_prefix.group(1), 'artist-prefix')
        # The same after normalization, which forgives "*NSYNC" vs "_NSYNC",
        # "I'm With Her" vs "im_with_her" and "(philip glass) title".
        normalized_base = normalize(base)
        if normalized_base.startswith(f'{normalized_artist} '):
            add(normalized_base[len(normalized_artist) + 1:], 'artist-prefix')

    # Numeric fields inside a scene-style name are strong track delimiters:
    # Artist - Album - 02 - Title, Album - 02 - Title, or Disc 1 - 02 - Title.
    parts = [part.strip() for part in re.split(r'\s+[-–]\s+', base)]
    for index, part in enumerate(parts[:-1]):
        if re.fullmatch(r'\d{1,3}', part) and index + 1 < len(parts):
            add(' - '.join(parts[index + 1:]), 'embedded-number', int(part))
    if len(parts) >= 2 and artist:
        # A collaborator list ("A, B - Title", "A; B - Title", "A with B -
        # Title") still opens with the artist; "Title - Artist" closes with it.
        leading = normalize(parts[0])
        if leading == normalized_artist or leading.startswith(f'{normalized_artist} '):
            add(' - '.join(parts[1:]), 'artist-segment')
        if normalize(parts[-1]) == normalized_artist:
            add(' - '.join(parts[:-1]), 'artist-suffix')
    if len(parts) >= 2 and album and normalize(_without_year(parts[0])) == normalize(_without_year(album)):
        add(' - '.join(parts[1:]), 'album-segment')

    normalized_base = normalize(base)
    normalized_album = normalize(album)
    for leading, source in (
        (f'{normalize(artist)} {normalized_album}', 'artist-album-number'),
        (normalized_album, 'album-number'),
    ):
        if leading and normalized_base.startswith(f'{leading} '):
            remainder = normalized_base[len(leading) + 1:]
            numbered_title = re.match(r'^(\d{1,3})\s+(.+)$', remainder)
            if numbered_title:
                add(numbered_title.group(2), source, int(numbered_title.group(1)))

    # Parent directories corroborate album/artist, but never become the song
    # title. Skip disc directories when selecting the album parent.
    return tuple(variants)


def _field(target: Any, key: str, default: Any = None) -> Any:
    return target.get(key, default) if isinstance(target, dict) else getattr(target, key, default)


def _track_number(value: Any) -> int | None:
    try:
        number = int(str(value).split('/')[0])
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _expected_position(target: Any) -> tuple[int | None, int | None]:
    number = _track_number(
        _field(target, 'track_number', None) or _field(target, 'trackNumber', None)
    )
    disc = _field(target, 'disc_number', None) or _field(target, 'discNumber', None)
    try:
        disc = int(disc) if disc else None
    except (TypeError, ValueError):
        disc = None
    return number, disc


def match_track(target: Any, candidate: Any, *, album: str = '') -> IdentityResult:
    """Grade how well one Soulseek file answers one requested track."""
    wanted = _title_key(_field(target, 'name', '') or _field(target, 'title', ''))
    if not wanted:
        return IdentityResult(False, 'missing-requested-title')
    artists = _field(target, 'artists', None) or []
    artist = artists[0] if artists else _field(target, 'artist', '')
    if isinstance(artist, dict):
        artist = artist.get('name', '')
    album = album or _field(target, 'album', '') or ''
    if isinstance(album, dict):
        album = album.get('name', '')
    filename = _field(candidate, 'filename', '')
    variants = title_interpretations(str(filename or ''), str(artist or ''), str(album or ''))
    parents = [normalize(_without_year(part)) for part in
               str(filename or '').replace('\\', '/').split('/')[:-1]]
    artist_evidence = bool(artist and normalize(artist) in parents)
    album_evidence = bool(album and normalize(_without_year(str(album))) in parents)
    expected_number, expected_disc = _expected_position(target)
    preferred_version = bool(_field(candidate, 'preferred_version_hit', False))

    best_similarity = 0.0
    for variant in variants:
        exact_title = variant.title == wanted
        versioned_title = (
            not exact_title
            and preferred_version
            and variant.title.startswith(f'{wanted} ')
            and bool(_VERSION_WORD.search(variant.title[len(wanted):]))
        )
        if not exact_title and not versioned_title:
            best_similarity = max(
                best_similarity, SequenceMatcher(None, wanted, variant.title).ratio(),
            )
            continue
        number_agrees = None
        if expected_number and variant.number:
            number_agrees = expected_number == variant.number
        if expected_disc and variant.disc:
            number_agrees = (number_agrees is not False) and expected_disc == variant.disc
        return IdentityResult(
            True, 'preferred-version-title' if versioned_title else 'title-match',
            variant.title, variant.source, variant.number, variant.disc,
            artist_evidence, album_evidence, number_agrees=number_agrees,
        )
    # A plausible parse that does not exactly equal the requested title is not
    # proof of a different recording: Soulseek names append release hashes,
    # session dates and other decoration, and equivalent titles differ in
    # punctuation. Leave the row to the confidence/artist/quality gates.
    if variants:
        return IdentityResult(False, 'parsed-title-mismatch', title_similarity=best_similarity)
    return IdentityResult(False, 'unrecognized-layout')


@dataclass(frozen=True)
class AlbumAssignment:
    pairs: tuple[tuple[int, int], ...]
    expected_count: int
    source_count: int

    @property
    def coverage(self) -> float:
        return len(self.pairs) / self.expected_count if self.expected_count else 0.0


def _edge_rank(result: IdentityResult) -> int | None:
    """Lower is stronger; None is no edge."""
    if result.matches:
        return {True: 0, None: 1, False: 2}[result.number_agrees]
    if result.title_similarity >= _WEAK_TITLE_SIMILARITY:
        return 3
    return None


def assign_album_tracks(expected: Sequence[Any], candidates: Sequence[Any], *, album: str = '') -> AlbumAssignment:
    """Maximum one-to-one coverage, preferring the strongest evidence per pair.

    Every requested track gets at most one file and every file serves at
    most one request. Edges are ranked (agreeing number, unknown number,
    disagreeing number, fuzzy title) so a renumbered edition or an
    unfamiliar layout still counts toward coverage while an exact,
    numbered match always wins the file when both exist.
    """
    edges: list[list[tuple[int, int]]] = [[] for _ in expected]
    for expected_index, target in enumerate(expected):
        for candidate_index, candidate in enumerate(candidates):
            rank = _edge_rank(match_track(target, candidate, album=album))
            if rank is not None:
                edges[expected_index].append((rank, candidate_index))
    owner: dict[int, int] = {}

    def augment(expected_index: int, seen: set[int]) -> bool:
        for _, candidate_index in sorted(edges[expected_index]):
            if candidate_index in seen:
                continue
            seen.add(candidate_index)
            if candidate_index not in owner or augment(owner[candidate_index], seen):
                owner[candidate_index] = expected_index
                return True
        return False

    for expected_index in sorted(range(len(expected)), key=lambda index: (len(edges[index]), index)):
        augment(expected_index, set())
    pairs = [(expected_index, candidate_index) for candidate_index, expected_index in owner.items()]
    return AlbumAssignment(tuple(sorted(pairs)), len(expected), len(candidates))
