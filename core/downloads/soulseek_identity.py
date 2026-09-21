"""Target-aware interpretation of Soulseek paths and album tracklists.

The generic filename parser has to choose one title without knowing what was
requested. Soulseek paths are too varied for that choice to be authoritative;
keep a small set of plausible titles and compare them to the requested track.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

from core.text.normalize import normalize_for_comparison


_YEAR = re.compile(r'(?<!\d)[\[(]?((?:19|20)\d{2})[\])]?')
_DISC_DIR = re.compile(r'^(?:disc|disk|cd)\s*[-._ ]*(\d{1,2})$', re.IGNORECASE)
_LEADING_DISC_NUMBER = re.compile(r'^\s*(\d{1,2})[-.](\d{1,2})\s*[-._ ]+')
_PACKED_DISC_NUMBER = re.compile(r'^\s*(0[1-9])(\d{2})\s*[-._ ]+')
_LEADING_NUMBER = re.compile(r'^\s*(?:\d{1,2}[-.]\d{1,2}|\d{1,3})\s*[-._ ]+\s*')
_AUDIO_EXTENSION = re.compile(r'\.[a-z0-9]{2,5}$', re.IGNORECASE)
_TECHNICAL_TAG = re.compile(r'\s*[\[(](?:flac|mp3|320kbps|v0|lossless|24bit|16bit|hi res)[\])]\s*', re.IGNORECASE)
_REMASTER_TAG = re.compile(r'\s*[\[(](?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?[\])]\s*', re.IGNORECASE)
_REMASTER_SUFFIX = re.compile(r'\s*[-–]\s*(?:(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)\s*$', re.IGNORECASE)
_FEAT_TAG = re.compile(r'\s*[\[(](?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]\s*', re.IGNORECASE)
_CLEAN_TAG = re.compile(r'\s*[\[(](?:explicit|clean)[\])]\s*', re.IGNORECASE)
_PREFERRED_VERSION_WORD = re.compile(
    r'\b(?:live|remix|mix|acoustic|instrumental|extended|demo|karaoke|radio edit|single edit)\b'
)


def normalize(text: Any) -> str:
    """Shared accent-aware comparison, retaining word boundaries."""
    folded = normalize_for_comparison(str(text or ''))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', folded)).strip()


def _title_key(text: str) -> str:
    """Ignore mastering/credit decorations, never recording versions."""
    text = _REMASTER_TAG.sub(' ', text)
    text = _REMASTER_SUFFIX.sub(' ', text)
    text = _FEAT_TAG.sub(' ', text)
    text = _CLEAN_TAG.sub(' ', text)
    text = re.sub(r"(?<=\w)['’](?=\w)", '', text)
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
    contradicts: bool = False


@lru_cache(maxsize=16384)
def title_interpretations(filename: str, artist: str = '', album: str = '') -> tuple[TitleEvidence, ...]:
    """Produce bounded interpretations, not a single guessed filename parse."""
    segments = [part.strip() for part in str(filename or '').replace('\\', '/').split('/') if part.strip()]
    if not segments:
        return ()
    stem = _AUDIO_EXTENSION.sub('', segments[-1]).strip()
    disc = None
    for segment in reversed(segments[:-1]):
        found = _DISC_DIR.fullmatch(segment)
        if found:
            disc = int(found.group(1))
            break
    packed_number = _PACKED_DISC_NUMBER.match(stem)
    leading_disc = _LEADING_DISC_NUMBER.match(stem) or packed_number
    if leading_disc:
        disc = int(leading_disc.group(1))
    number_match = packed_number or _LEADING_NUMBER.match(stem)
    number = None
    if packed_number:
        number = int(packed_number.group(2))
    elif number_match:
        digits = re.findall(r'\d+', number_match.group())
        number = int(digits[-1]) if digits else None
    variants: list[TitleEvidence] = []
    seen: set[str] = set()

    def add(text: str, source: str, parsed_number: int | None = number) -> None:
        key = _title_key(text)
        if key and key not in seen and len(variants) < 16:
            seen.add(key)
            variants.append(TitleEvidence(key, source, parsed_number, disc))

    cleaned_stem = _TECHNICAL_TAG.sub(' ', stem)
    base = (_PACKED_DISC_NUMBER.sub('', cleaned_stem, count=1)
            if _PACKED_DISC_NUMBER.match(cleaned_stem)
            else _LEADING_NUMBER.sub('', cleaned_stem, count=1)).strip(' -_.')
    # A number followed only by whitespace can be part of the actual title.
    if re.match(r'^\s*\d+\s+[A-Za-z]', cleaned_stem):
        add(cleaned_stem, 'literal-leading-number', None)
    add(base, 'basename')
    if artist:
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
            found_prefix = featured_prefix.match(base) or plain_prefix.match(base)
            if found_prefix:
                add(found_prefix.group(1), 'artist-prefix')

    # Numeric fields inside a scene-style name are strong track delimiters:
    # Artist - Album - 02 - Title, Album - 02 - Title, or Disc 1 - 02 - Title.
    parts = [part.strip() for part in re.split(r'\s+[-–]\s+', base)]
    for index, part in enumerate(parts[:-1]):
        if re.fullmatch(r'\d{1,3}', part) and index + 1 < len(parts):
            add(' - '.join(parts[index + 1:]), 'embedded-number', int(part))
    if len(parts) >= 2 and artist and normalize(parts[0]) == normalize(artist):
        add(' - '.join(parts[1:]), 'artist-segment')
    if len(parts) >= 2 and album and normalize(_without_year(parts[0])) == normalize(_without_year(album)):
        add(' - '.join(parts[1:]), 'album-segment')

    normalized_base = normalize(base)
    normalized_album = normalize(album)
    for prefix, source in (
        (f'{normalize(artist)} {normalized_album}', 'artist-album-number'),
        (normalized_album, 'album-number'),
    ):
        if prefix and normalized_base.startswith(f'{prefix} '):
            remainder = normalized_base[len(prefix) + 1:]
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


def match_track(target: Any, candidate: Any, *, album: str = '') -> IdentityResult:
    """Identify exact matches and clear contradictions; leave unknown layouts open."""
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
    expected_number = _track_number(
        _field(target, 'track_number', None) or _field(target, 'trackNumber', None)
    )
    expected_disc = _field(target, 'disc_number', None) or _field(target, 'discNumber', None)
    try:
        expected_disc = int(expected_disc) if expected_disc else None
    except (TypeError, ValueError):
        expected_disc = None
    conflicting_number = False
    for variant in variants:
        exact_title = variant.title == wanted
        preferred_version_title = (
            not exact_title
            and bool(_field(candidate, 'preferred_version_hit', False))
            and variant.title.startswith(f'{wanted} ')
            and bool(_PREFERRED_VERSION_WORD.search(variant.title[len(wanted):]))
        )
        if not exact_title and not preferred_version_title:
            continue
        if expected_number and variant.number and expected_number != variant.number:
            conflicting_number = True
            continue
        if expected_disc and variant.disc and expected_disc != variant.disc:
            conflicting_number = True
            continue
        return IdentityResult(True, 'preferred-version-title' if preferred_version_title else 'title-match',
                              variant.title, variant.source,
                              variant.number, variant.disc, artist_evidence,
                              album_evidence)
    if conflicting_number:
        return IdentityResult(False, 'number-or-disc-mismatch', contradicts=True)
    # A plausible parse that does not exactly equal the requested title is not
    # proof that this is a different recording. Soulseek names commonly append
    # release hashes, mix labels, session dates, or other useful metadata, and
    # equivalent titles can use different punctuation. Keep those rows open to
    # the existing confidence/artist/quality gates. Only concrete number/disc
    # conflicts above are authoritative negative evidence.
    if variants:
        return IdentityResult(False, 'parsed-title-mismatch')
    return IdentityResult(False, 'unrecognized-layout')


@dataclass(frozen=True)
class AlbumAssignment:
    pairs: tuple[tuple[int, int], ...]
    expected_count: int
    source_count: int

    @property
    def coverage(self) -> float:
        return len(self.pairs) / self.expected_count if self.expected_count else 0.0


def assign_album_tracks(expected: Sequence[Any], candidates: Sequence[Any], *, album: str = '') -> AlbumAssignment:
    """Maximum one-to-one coverage with stable number-aware tie ordering."""
    edges: list[list[tuple[bool, int]]] = [[] for _ in expected]
    for expected_index, target in enumerate(expected):
        for candidate_index, candidate in enumerate(candidates):
            result = match_track(target, candidate, album=album)
            if result.matches:
                number = _track_number(
                    _field(target, 'track_number', None) or _field(target, 'trackNumber', None)
                )
                number_agrees = bool(number and result.number and number == result.number)
                edges[expected_index].append((not number_agrees, candidate_index))
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
