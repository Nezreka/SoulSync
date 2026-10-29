"""Release title and filename presentation rules."""
from __future__ import annotations

import os
import re

from core.matching.audio_verification import normalize
from core.reconciliation.plan import (
    display_title_compatible,
)

_ALBUM_EDITION_WORD = re.compile(
    r'\b(?:anniversary|bit|cd|deluxe|edition|expanded|khz|limited|lp|master|mono|'
    r'pressing|reissue|remaster(?:ed)?|special|stereo|target|vinyl)\b', re.I)


_ALBUM_MATERIAL_QUALIFIER = re.compile(
    r"\b(?:acoustic|anniversary|bit|collector(?:s|'s)?|deluxe|demo|edition|expanded|"
    r'instrumental|khz|live|master|mono|remix|reissue|remaster(?:ed)?|special|'
    r'stereo|soundtrack|version)\b', re.I)


_SOUNDTRACK_SUFFIX = re.compile(
    r'\s*[\[(]?(?:original\s+(?:motion\s+picture|television)\s+)?soundtrack[\])]?$|'
    r'\s*[\[(]?music\s+from\s+the\s+(?:motion\s+picture|film)[\])]?$', re.I)


_PRESSING_PLANT_DETAIL = re.compile(
    r'\b(?:EDC\s+Blackburn|EDC\s+Germany|Blackburn\s+Ltd\.?|'
    r'Columbia\s+House|club\s+edition|manufactured\s+by)\b', re.I)


def _clean_album_display_title(title: str) -> str:
    """Drop known manufacturing flavor while preserving a named edition."""
    match = (re.match(r'^(.*?)\s+\(([^()]*)\)\s*$', title.strip()) or
             re.match(r'^(.*?)\s+\[([^\[\]]*)\]\s*$', title.strip()))
    if not match or not _PRESSING_PLANT_DETAIL.search(match.group(2)):
        return title
    parts = [part.strip() for part in re.split(r'[,;/]', match.group(2))]
    kept = [part for part in parts if part and not _PRESSING_PLANT_DETAIL.search(part)]
    return f'{match.group(1)} ({", ".join(kept)})' if kept else match.group(1)



def _suggest_filename(path: str, slot: dict, *, absolute_number: int | None = None) -> str:
    """Keep the file's numbering convention while correcting a clear slot."""
    if not str(slot.get('title') or '').strip():
        return ''
    stem, extension = os.path.splitext(os.path.basename(path))
    from core.imports.paths import sanitize_filename
    from core.text.track_prefix import parse_track_prefix
    parsed = parse_track_prefix(stem)
    if parsed.number is not None:
        prefix = stem[:-len(parsed.remainder)]
        track, disc = int(slot.get('track_number') or 0), int(slot.get('disc_number') or 1)
        # A single-folder album may use absolute filenames with per-disc tags.
        # Count against the complete release, including absent slots.
        compatible = (parsed.number == track and parsed.disc in (None, disc) or
                      parsed.disc is None and parsed.number == absolute_number)
        if not compatible and not parsed.side and track > 0:
            numbers = list(re.finditer(r'\d+', prefix))
            values = ([disc, track] if parsed.disc is not None and len(numbers) == 2
                      else [disc * 100 + track] if parsed.disc is not None else [track])
            if parsed.disc is not None and len(numbers) == 1 and not (disc <= 9 and track < 100):
                # Packed DDTT cannot encode these positions. Preserve the
                # prefix rather than making it look like another disc/track.
                values = []
            for match, value in reversed(list(zip(numbers, values))):
                prefix = prefix[:match.start()] + str(value).zfill(len(match[0])) + prefix[match.end():]
        title = (parsed.remainder if display_title_compatible(parsed.remainder, slot)
                 else sanitize_filename(slot['title']))
        proposed = f'{prefix}{title}{extension}'
    else:
        # Preserve an unrecognized numeric prefix (for example a year). It is
        # not evidence of a track position and must not be rewritten as one.
        unknown = re.match(r'^(\d{4,}\s*[-_.]\s*)(.+)$', stem)
        current = unknown[2] if unknown else stem
        title = (current if display_title_compatible(current, slot)
                 else sanitize_filename(slot['title']))
        proposed = f'{unknown[1] if unknown else ""}{title}{extension}'
    return os.path.join(os.path.dirname(path), proposed) if proposed != os.path.basename(path) else ''



def _release_display_metadata(source, release_id, meta):
    """Use the provider adapters for covers and complete release credits."""
    from core.metadata.types import Album
    converter = getattr(Album, f'from_{source}_dict', None)
    adapted = converter(meta) if converter else None
    credits = meta.get('artists') or meta.get('artist-credit') or []
    names = [str(artist.get('name') or (artist.get('artist') or {}).get('name') or '')
             if isinstance(artist, dict) else str(artist) for artist in credits]
    names = [name for name in names if name]
    if not names and adapted:
        names = [name for name in adapted.artists if name and name != 'Unknown Artist']
    return {
        'release_artist': ' & '.join(names),
        'image_url': meta.get('image_url') or meta.get('thumb_url') or
            next((image.get('url') for image in meta.get('images') or []
                  if isinstance(image, dict) and image.get('url')), '') or
            (adapted.image_url if adapted else '') or
            (f'https://coverartarchive.org/release/{release_id}/front-250' if source == 'musicbrainz' else ''),
    }



def _trailing_album_qualifier(value: str) -> tuple[str, str] | None:
    """Find one balanced trailing qualifier, including nested pressing detail."""
    value = value.strip()
    if not value or value[-1] not in ')]':
        return None
    opening = '(' if value[-1] == ')' else '['
    closing = value[-1]
    depth = 0
    for index in range(len(value) - 1, -1, -1):
        if value[index] == closing:
            depth += 1
        elif value[index] == opening:
            depth -= 1
            if depth == 0:
                if index and value[index - 1].isspace():
                    return value[:index].strip(), value[index + 1:-1]
                return None
    return None



def _album_titles_compatible(current: str, release: str) -> bool:
    """Keep local edition detail when both names share the same album identity."""

    def without_repeated_qualifier(value: str) -> str:
        qualified = _trailing_album_qualifier(value)
        if not qualified:
            return value
        base, qualifier = qualified
        base_words = normalize(base)
        qualifier_words = normalize(qualifier)
        if qualifier_words and f' {qualifier_words} ' in f' {base_words} ':
            return base.strip()
        return value

    cleaned_current = without_repeated_qualifier(current or '')
    cleaned_release = without_repeated_qualifier(release or '')
    current_normalized = normalize(cleaned_current)
    release_normalized = normalize(cleaned_release)
    if not current_normalized or not release_normalized:
        return False
    current_qualifier = _trailing_album_qualifier(cleaned_current)
    release_qualifier = _trailing_album_qualifier(cleaned_release)
    if current_qualifier or release_qualifier:
        current_base, current_detail = current_qualifier or (cleaned_current, '')
        release_base, release_detail = release_qualifier or (cleaned_release, '')
        if normalize(current_base) != normalize(release_base):
            return False
        release_is_material = bool(_ALBUM_MATERIAL_QUALIFIER.search(release_detail))
        current_is_material = bool(_ALBUM_MATERIAL_QUALIFIER.search(current_detail))
        if release_detail and not release_is_material:
            # Retailer, pressing-plant and record-club labels identify the
            # catalog release. They are not useful display-title metadata.
            return True
        if not release_detail:
            # Preserve useful detail that is already present locally when the
            # selected release uses only the base title.
            return True
        if not current_detail or not current_is_material:
            return False
        return normalize(current_detail) == normalize(release_detail)
    if current_normalized == release_normalized:
        # Some normalizers intentionally collapse known title flavor words
        # (for example R and Rated R). The shorter local spelling must not
        # suppress a fuller provider title when neither side has a qualifier.
        return len(cleaned_current.strip()) >= len(cleaned_release.strip())
    if current_normalized.startswith(f'{release_normalized} '):
        local_detail = current_normalized[len(release_normalized):].strip()
        return bool(_ALBUM_EDITION_WORD.search(local_detail))
    return False



def _display_album_title(files: list[dict], library_title: str,
                         release_title: str, policy: str = 'preserve') -> str:
    """Prefer one consistent, clean local title over catalog-only qualifiers."""
    if policy == 'exact_release':
        return release_title
    library_title = _clean_album_display_title(library_title or '')
    tag_titles = [str((file.get('tags') or {}).get('album') or '').strip()
                  for file in files]
    tag_titles = [_clean_album_display_title(title) for title in tag_titles if title]
    if tag_titles:
        normalized = {re.sub(r'\s+', ' ', title).casefold() for title in tag_titles}
        library_qualifier = _trailing_album_qualifier(library_title)
        # Physical pressing descriptions belong to the release evidence. Only
        # clean them when all files and the selected release agree on the base
        # title; never erase a local live/remix/deluxe title merely for brevity.
        pressing_only = bool(library_qualifier
            and re.search(r'\b(?:black|white|colou?red|gold|silver)\s+(?:disc|vinyl)\b',
                          library_qualifier[1], re.I)
            and not re.search(r'\b(?:live|remix|acoustic|instrumental|remaster(?:ed)?)\b',
                              library_qualifier[1], re.I)
            and normalize(release_title) == normalize(tag_titles[0]))
        clean_library_variant = bool(
            library_qualifier and
            normalize(tag_titles[0]) == normalize(library_qualifier[0]) and
            (pressing_only or not _ALBUM_MATERIAL_QUALIFIER.search(library_qualifier[1]))
        )
        if len(normalized) == 1 and clean_library_variant:
            return tag_titles[0]
    if library_title and _album_title_preservable(library_title, release_title):
        return library_title
    if (tag_titles and len(normalized) == 1 and
            _album_title_preservable(tag_titles[0], release_title)):
        return tag_titles[0]
    return release_title



def _album_title_preservable(current: str, release: str) -> bool:
    if _album_titles_compatible(current, release):
        return True
    # A catalog edition can be exact while a consistent local display title
    # intentionally omits its qualifier. Keep the release ID as the identity.
    qualified = _trailing_album_qualifier(release)
    return bool(qualified and current.strip() and
                normalize(current) == normalize(qualified[0]))

