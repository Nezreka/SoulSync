"""Enriched downloads from basic search: match the picked files to a release on
ONE metadata provider, then download them tagged and filed as that release.

the old vanilla modal mixed providers (spotify suggestions, fallback-client
search, then spotify again for the ids) and did the file->track mapping in the
browser. here the provider is chosen once and every lookup asks that provider
and only that provider. a lookup that comes back from a different one is
refused, not quietly used.

the mapping reuses the import matcher (match_files_to_tracks), the same one
auto-import trusts.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("search.enriched_download")


class ProviderMismatch(Exception):
    """the provider answered with a different provider's data"""


# ── the files the user picked ─────────────────────────────────────────────


def file_key(file: Dict[str, Any]) -> str:
    return f"{file.get('username', '')}::{file.get('filename', '')}"


def _title_from_filename(filename: str) -> str:
    # soulseek paths use backslashes, os.path.basename won't split them on linux
    stem = os.path.splitext(str(filename or '').replace('\\', '/').rsplit('/', 1)[-1])[0]
    try:
        from core.imports.filename import parse_filename_metadata
        parsed = parse_filename_metadata(str(filename or '')) or {}
        if parsed.get('title'):
            return str(parsed['title'])
    except Exception as exc:  # noqa: BLE001 - the stem is a fine fallback
        logger.debug("filename parse failed for %s: %s", filename, exc)
    return stem


def file_tags_for(files: List[Dict[str, Any]], album_name: str) -> Dict[str, dict]:
    """what the matcher reads for each file, built from the search rows (the
    files aren't on disk yet). title is ALWAYS filled, the matcher's own
    fallback can't split a soulseek path"""
    tags = {}
    for file in files:
        tags[file_key(file)] = {
            'title': file.get('title') or _title_from_filename(file.get('filename', '')),
            'artist': file.get('artist') or '',
            'album': file.get('album') or album_name,
            'track_number': file.get('track_number') or 0,
            'disc_number': file.get('disc_number') or 1,
            'duration_ms': file.get('duration') or 0,
        }
    return tags


# ── the release ───────────────────────────────────────────────────────────


def fetch_release(
    source: str,
    album_id: str,
    album_name: str,
    artist: str,
    *,
    get_album_tracks: Optional[Callable[..., dict]] = None,
) -> Dict[str, Any]:
    """the chosen release's album info and tracklist, from `source` only"""
    if get_album_tracks is None:
        from core.metadata.album_tracks import get_artist_album_tracks as get_album_tracks
    result = get_album_tracks(
        album_id, artist_name=artist, album_name=album_name, source_override=source,
    ) or {}
    if not result.get('success'):
        raise LookupError(result.get('error') or 'Could not load that release')
    served = str(result.get('source') or '').lower()
    if served and served != str(source).lower():
        # get_artist_album_tracks falls back across providers. that's the
        # mixed-provider bug the old modal had, so it's refused here
        raise ProviderMismatch(f"{source} didn't answer, {served} did")
    tracks = [t for t in (result.get('tracks') or []) if isinstance(t, dict)]
    album = dict(result.get('album') or {})
    album.setdefault('id', album_id)
    album.setdefault('name', album_name)
    album['source'] = source
    total_discs = max([int(t.get('disc_number') or 1) for t in tracks] or [1])
    album['total_discs'] = total_discs
    album.setdefault('total_tracks', len(tracks))
    return {'album': album, 'tracks': tracks, 'total_discs': total_discs}


def match_files(
    files: List[Dict[str, Any]],
    tracks: List[Dict[str, Any]],
    album_name: str,
    *,
    matcher: Optional[Callable[..., dict]] = None,
    quality_rank: Optional[Callable[[str], int]] = None,
) -> List[Dict[str, Any]]:
    """[{file_key, track_index (or None), confidence}] in file order"""
    if matcher is None:
        from core.imports.album_matching import default_quality_rank, match_files_to_tracks as matcher
        quality_rank = quality_rank or default_quality_rank
    keys = [file_key(f) for f in files]
    result = matcher(
        keys, file_tags_for(files, album_name), tracks,
        target_album=album_name, quality_rank=quality_rank or (lambda _p: 0),
    ) or {}
    # re-key by object identity, track ids can be empty and collide
    index_of = {id(track): i for i, track in enumerate(tracks)}
    by_file = {}
    for match in result.get('matches') or []:
        track_index = index_of.get(id(match.get('track')))
        if track_index is None:
            continue
        by_file[match.get('file')] = (track_index, float(match.get('confidence') or 0))
    out = []
    for key in keys:
        track_index, confidence = by_file.get(key, (None, 0.0))
        out.append({'file_key': key, 'track_index': track_index, 'confidence': round(confidence, 3)})
    return out


# ── what each task carries ────────────────────────────────────────────────


def _artist_names(value: Any) -> List[str]:
    names = []
    for artist in value or []:
        name = artist.get('name') if isinstance(artist, dict) else artist
        if name:
            names.append(str(name))
    return names


def album_context(album: Dict[str, Any], source: str, fallback_artist: str) -> Dict[str, Any]:
    names = _artist_names(album.get('artists')) or [album.get('artist') or album.get('artist_name') or fallback_artist]
    image = album.get('image_url') or ''
    if not image and isinstance(album.get('images'), list) and album['images']:
        first = album['images'][0]
        image = first.get('url', '') if isinstance(first, dict) else ''
    return {
        'id': str(album.get('id') or ''),
        'name': album.get('name') or '',
        'release_date': album.get('release_date') or '',
        'image_url': image,
        'images': album.get('images') or ([{'url': image}] if image else []),
        'album_type': album.get('album_type') or 'album',
        'total_tracks': album.get('total_tracks') or 0,
        'total_discs': album.get('total_discs') or 1,
        'artists': [{'name': n} for n in names if n],
        'source': source,
    }


def enriched_track_info(
    track: Dict[str, Any], album_ctx: Dict[str, Any], source: str, *, as_album: bool,
) -> Dict[str, Any]:
    """track_info in the shape attempt_download_with_candidates reads: with
    _is_explicit_album_download it uses the explicit album/artist context as
    is, so the tags and the folder come from the release the user chose"""
    artists = _artist_names(track.get('artists')) or [a['name'] for a in album_ctx.get('artists', [])]
    album_artist = (album_ctx.get('artists') or [{'name': artists[0] if artists else ''}])[0]
    info = {
        'id': str(track.get('id') or ''),
        'name': track.get('name') or '',
        'artists': [{'name': n} for n in artists],
        'album': album_ctx,
        'duration_ms': track.get('duration_ms') or 0,
        'track_number': track.get('track_number') or 0,
        'disc_number': track.get('disc_number') or 1,
        'source': source,
    }
    if as_album:
        info['_is_explicit_album_download'] = True
        info['_explicit_album_context'] = album_ctx
        info['_explicit_artist_context'] = {'name': album_artist.get('name', ''), 'id': '', 'genres': [], 'source': source}
    return info


def single_track_details(source: str, track_id: str, *, client_for_source: Optional[Callable] = None) -> Optional[dict]:
    """one track by id, from that provider only. None when it can't say"""
    if client_for_source is None:
        from core.metadata.registry import get_client_for_source as client_for_source
    client = client_for_source(source)
    if client is None or not hasattr(client, 'get_track_details'):
        return None
    try:
        if source == 'spotify':
            return client.get_track_details(track_id, allow_fallback=False)
        return client.get_track_details(track_id)
    except Exception as exc:  # noqa: BLE001 - the search row is the fallback
        logger.debug("[Enriched] %s track %s lookup failed: %s", source, track_id, exc)
        return None
