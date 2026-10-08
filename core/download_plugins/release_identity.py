"""Conservative indexer release identity; never a recording/album identity.

No URL, GUID, group alone, fuzzy title or size tolerance enters the key.
Only Usenet rows participate. A complete original release name with markers
and an exact positive size is a cautious heuristic for duplicate NZB listings.
"""
from __future__ import annotations

from copy import copy
import hashlib
import json
import re

from core.quality.release_format import audio_quality_from_release

_RELEASE_PROTOCOLS = frozenset({'usenet'})
_YEAR = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')
_SCENE_GROUP = re.compile(r'-(?!\d+$)[a-z][a-z0-9]{1,20}(?:[ ._-]+int)?$', re.I)
_RELEASE_MARKER = re.compile(r'(?<![a-z0-9])(?:web|cd|\d+cd|vinyl|\d+lp|flac|alac|mp3|aac|opus|wav|ape)(?![a-z0-9])', re.I)


def release_key(protocol, title, size):
    protocol = str(protocol or '').strip().lower()
    if protocol not in _RELEASE_PROTOCOLS:
        return None
    name = ' '.join(str(title or '').split()).casefold()
    # Bare artist/album labels do not distinguish editions or separate rips.
    # Keep ALL punctuation and every quality/edition/year/repack/group token.
    if not name or not _YEAR.search(name) or not (_RELEASE_MARKER.search(name) or _SCENE_GROUP.search(name)):
        return None
    try:
        size = int(size)
    except (TypeError, ValueError, OverflowError):
        return None
    return (protocol, 'name-size', name, size) if size > 0 else None


def prowlarr_release_key(row):
    key = release_key(row.protocol, row.title, row.size)
    return _with_quality_evidence(key, row)


def candidate_release_key(row):
    protocol = str(getattr(row, 'username', '') or '').lower()
    if protocol not in _RELEASE_PROTOCOLS:
        return None
    metadata = getattr(row, '_source_metadata', None) or {}
    if metadata.get('protocol', protocol) != protocol:
        return None
    key = release_key(protocol, metadata.get('release_title'), getattr(row, 'size', 0))
    return _with_quality_evidence(key, row)


def candidate_release_id(row):
    """Stable across fresh opaque tokens; contains no download credentials."""
    key = candidate_release_key(row)
    if key is None:
        return None
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def candidate_endpoint_id(row):
    """A renewed signed URL must not reset an indexer's attempt history."""
    release_id = candidate_release_id(row)
    indexer = (getattr(row, '_source_metadata', None) or {}).get('indexer_id')
    return f'{release_id}:{indexer}' if release_id and indexer else None


def release_sources(row):
    """Flatten source alternatives without cycles or duplicate endpoint tokens."""
    if (isinstance(row, dict)
            or getattr(row, 'protocol', getattr(row, 'username', None)) != 'usenet'
            or not getattr(row, '_release_sources', None)):
        return [row]
    out, seen = [], set()
    pending = [row]
    while pending:
        source = pending.pop(0)
        # Projected rows contain opaque server tokens, never indexer URLs.
        if hasattr(source, 'filename'):
            key = (source.username, source.filename)
        else:
            key = (source.indexer_id, source.guid or source.download_url or source.magnet_uri or source.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
        pending.extend(getattr(source, '_release_sources', ()) or ())
    return out


def release_evidence(row):
    """Consistent ranking/grab facts, separate from immutable endpoint labels."""
    if getattr(row, 'protocol', getattr(row, 'username', None)) != 'usenet':
        return row
    return getattr(row, '_release_evidence', None) or row


def _quality(row):
    if hasattr(row, 'audio_quality'):
        return row.audio_quality
    return audio_quality_from_release(row.title, row.categories)


def _with_quality_evidence(key, row):
    if key and key[1] == 'name-size':
        aq = _quality(row)
        return key + (tuple(getattr(aq, field, None) for field in
                            ('format', 'bitrate', 'bit_depth', 'sample_rate')),)
    return key


def _release_title(row):
    if hasattr(row, 'filename'):
        return str((getattr(row, '_source_metadata', None) or {}).get('release_title') or '')
    return str(row.title or '')


def compatible_quality(left, right):
    # Contradictory advertised editions must keep separate release slots.
    x, y = _release_title(left).casefold(), _release_title(right).casefold()
    years_x, years_y = set(_YEAR.findall(x)), set(_YEAR.findall(y))
    if years_x and years_y and years_x != years_y:
        return False
    if years_x and years_y and (_RELEASE_MARKER.search(x) or _SCENE_GROUP.search(x)) and (_RELEASE_MARKER.search(y) or _SCENE_GROUP.search(y)):
        if ' '.join(x.split()) != ' '.join(y.split()):
            return False
    a, b = _quality(left), _quality(right)
    if a is None or b is None:
        return True
    for field in ('format', 'bitrate', 'bit_depth', 'sample_rate'):
        x, y = getattr(a, field, None), getattr(b, field, None)
        if x and y and x != 'unknown' and y != 'unknown' and x != y:
            return False
    return True


def merge_release_sources(winner, other):
    """Keep ranking's winner, with independent flat copies of its sources."""
    sources = release_sources(winner) + release_sources(other)
    merged = copy(winner)
    merged._release_sources = []
    seen = {(winner.username, winner.filename)} if hasattr(winner, 'filename') else {
        (winner.indexer_id, winner.guid or winner.download_url or winner.magnet_uri or winner.title)}
    for source in sources:
        key = (source.username, source.filename) if hasattr(source, 'filename') else (
            source.indexer_id, source.guid or source.download_url or source.magnet_uri or source.title)
        if key not in seen:
            seen.add(key)
            alt = copy(source)
            alt._release_sources = []
            merged._release_sources.append(alt)
    if not hasattr(winner, 'filename'):
        # Keep every endpoint's original title/categories for later conflict
        # checks. Enrichment is independent ranking/grab evidence: replacing
        # the preferred title here could erase its edition or REPACK evidence.
        def evidence_count(row):
            aq = _quality(row)
            fields = sum(bool(getattr(aq, field, None)) and getattr(aq, field, None) != 'unknown'
                         for field in ('format', 'bitrate', 'bit_depth', 'sample_rate'))
            structured = bool(_YEAR.search(row.title) and (_RELEASE_MARKER.search(row.title) or _SCENE_GROUP.search(row.title)))
            return fields, structured
        evidence = max(sources, key=evidence_count)
        merged._release_evidence = copy(evidence)
        merged._release_evidence._release_sources = []
        merged._release_evidence._release_evidence = None
    return merged


def _source_preference(row):
    return (not bool(row.download_url), getattr(row, 'indexer_priority', 25))


def dedupe_prowlarr_releases(rows):
    """One release per slot, preferred indexer first, preserving all endpoints.

    This precedes plugin projection, album selection and any candidate cap.
    Prowlarr priority only selects an endpoint inside the SAME release; it
    never competes with the user's quality-profile ranking between releases.
    """
    out, positions = [], {}
    for row in rows:
        key = prowlarr_release_key(row)
        positions_for_key = positions.get(key, []) if key is not None else []
        pos = next((i for i in positions_for_key
                    if all(compatible_quality(left, right)
                           for left in release_sources(out[i]) for right in release_sources(row))), None)
        if pos is None:
            out.append(row)
            if key is not None:
                positions.setdefault(key, []).append(len(out) - 1)
        else:
            winner, other = (row, out[pos]) if _source_preference(row) < _source_preference(out[pos]) else (out[pos], row)
            out[pos] = merge_release_sources(winner, other)
    return out


def dedupe_release_candidates(ranked):
    """Collapse release sightings only; input order is the quality ranking."""
    out, positions = [], {}
    for row in ranked:
        key = candidate_release_key(row)
        held = positions.get(key, []) if key is not None else []
        pos = next((i for i in held if all(
            compatible_quality(left, right)
            for left in release_sources(out[i]) for right in release_sources(row)
        )), None)
        if pos is None:
            out.append(row)
            if key is not None:
                positions.setdefault(key, []).append(len(out) - 1)
        else:
            out[pos] = merge_release_sources(out[pos], row)
    return out
