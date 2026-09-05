"""Reusable, conservative genre candidate collection and translation."""

import hashlib
import json
from difflib import SequenceMatcher

from core.genre_filter import DEFAULT_GENRES, _normalize_for_match
from core.metadata.cache import MetadataCache

PROVIDER_CONFIDENCE = {
    'canonical': 1.0, 'discogs': .90, 'spotify': .85, 'deezer': .80,
    'lastfm': .75, 'itunes': .70, 'audiodb': .65, 'embedded': .60,
}


def parse_values(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    try:
        parsed = json.loads(str(value))
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        if isinstance(parsed, dict):
            return [str(parsed.get('name')).strip()] if parsed.get('name') else []
    except (TypeError, ValueError):
        pass
    return [x.strip() for x in str(value).split(',') if x.strip()]


def whitelist_from_config(config_manager) -> list[str]:
    values = config_manager.get('genre_whitelist.genres', None) if config_manager else None
    return [str(x).strip() for x in (values if isinstance(values, list) else DEFAULT_GENRES) if str(x).strip()]


def whitelist_hash(whitelist: list[str]) -> str:
    return hashlib.sha256(json.dumps(whitelist, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def translate_genre(raw: str, whitelist: list[str], cache=None, run_cache=None) -> dict:
    normalized = _normalize_for_match(raw)
    key = (whitelist_hash(whitelist), normalized)
    if run_cache is not None and key in run_cache:
        return dict(run_cache[key], cache_hit=True)
    cached = cache.get_genre_translation(*key) if cache else None
    if cached:
        result = {'status': cached['status'], 'matched_genre': cached.get('matched_genre'),
                  'score': cached.get('score'), 'margin': cached.get('margin'),
                  'candidates': json.loads(cached.get('candidates_json') or '[]'), 'cache_hit': True}
        if run_cache is not None: run_cache[key] = result
        return result

    exact = next((g for g in whitelist if _normalize_for_match(g) == normalized), None)
    if exact:
        result = {'status': 'accepted', 'matched_genre': exact, 'score': 1.0, 'margin': 1.0, 'candidates': [exact]}
    else:
        tokens = set(normalized.split())
        scored = []
        for candidate in whitelist:
            cn = _normalize_for_match(candidate)
            char = SequenceMatcher(None, normalized, cn).ratio()
            ct = set(cn.split())
            overlap = len(tokens & ct) / max(1, len(tokens | ct))
            score = .7 * char + .3 * overlap
            scored.append((score, candidate))
        scored.sort(key=lambda x: (-x[0], _normalize_for_match(x[1])))
        best = scored[0] if scored else (0.0, None)
        second = scored[1][0] if len(scored) > 1 else 0.0
        margin = best[0] - second
        status = 'accepted' if best[0] >= .90 or (best[0] >= .84 and margin >= .08) else ('ambiguous' if best[0] > .70 else 'rejected')
        result = {'status': status, 'matched_genre': best[1] if status == 'accepted' else None,
                  'score': round(best[0], 4), 'margin': round(margin, 4),
                  'candidates': [x[1] for x in scored[:5]]}
    if cache:
        cache.store_genre_translation(key[0], raw, normalized, result)
    if run_cache is not None: run_cache[key] = result
    return result


def extract_provider_genres(source: str, entity_type: str, payload: dict) -> list[str]:
    if not isinstance(payload, dict): return []
    if source == 'spotify': return parse_values(payload.get('genres'))
    if source == 'itunes': return parse_values(payload.get('primaryGenreName') or payload.get('genre'))
    if source == 'deezer':
        genres = payload.get('genres', {})
        genres = genres.get('data', []) if isinstance(genres, dict) else genres
        values = [g.get('name') for g in genres if isinstance(g, dict) and g.get('name')] if isinstance(genres, list) else []
        if values:
            return values
        genre_id = payload.get('genre_id')
        try:
            mapped = MetadataCache._DEEZER_GENRE_MAP.get(int(genre_id)) if genre_id is not None else None
        except (TypeError, ValueError):
            mapped = None
        return [mapped] if mapped else []
    if source == 'discogs': return parse_values(payload.get('genres')) + parse_values(payload.get('styles'))
    if source == 'audiodb': return parse_values(payload.get('strGenre'))
    if source == 'lastfm': return parse_values(payload.get('lastfm_tags') or payload.get('tags'))
    return []  # MusicBrainz is deliberately not a genre source here.


def _mapping(value) -> dict:
    """Return a JSON/object value as a mapping, tolerating corrupt old rows."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def provider_ids_from_row(row: dict, entity_type: str) -> dict[str, str]:
    """Read provider identities from either a legacy or Library-v2 row.

    Library v2 promotes Spotify/MusicBrainz and keeps every other provider in
    ``external_ids``.  The enrichment feature arrived on dev using only the
    legacy per-provider columns; accepting both shapes keeps its pure candidate
    logic reusable while the runtime catalogue remains Library v2 only.
    """
    external = _mapping(row.get('external_ids'))
    ids = {str(source): str(value) for source, value in external.items()
           if source and value not in (None, '')}
    legacy_fields = {
        'spotify': ('spotify_artist_id' if entity_type == 'artist' else 'spotify_album_id'),
        'itunes': ('itunes_artist_id' if entity_type == 'artist' else 'itunes_album_id'),
        'deezer': 'deezer_id',
        'discogs': 'discogs_id',
        'audiodb': 'audiodb_id',
    }
    for source, field in legacy_fields.items():
        value = row.get(field)
        if source == 'deezer' and not value:
            value = row.get('deezer_artist_id' if entity_type == 'artist' else 'deezer_album_id')
        if value not in (None, ''):
            ids[source] = str(value)
    if row.get('spotify_id') not in (None, ''):
        ids['spotify'] = str(row['spotify_id'])
    if row.get('musicbrainz_id') not in (None, ''):
        ids['musicbrainz'] = str(row['musicbrainz_id'])
    return ids


def collect_local_candidates(row: dict) -> list[dict]:
    out = []
    for source, field, id_field in [
        ('discogs', 'discogs_genres', 'discogs_id'),
        ('discogs', 'discogs_styles', 'discogs_id'),
        ('lastfm', 'lastfm_tags', None),
    ]:
        for value in parse_values(row.get(field)):
            out.append({'raw_genre': value, 'source': source,
                        'source_entity_id': row.get(id_field) if id_field else None,
                        'origin': 'library'})
    # Native workers keep provider-specific payloads in lib2's enrichment JSON
    # instead of recreating the wide legacy columns above.
    enrichment = _mapping(row.get('enrichment'))
    external_ids = _mapping(row.get('external_ids'))
    for source, field in (
        ('discogs', 'genres'), ('discogs', 'styles'), ('lastfm', 'tags'),
    ):
        bucket = enrichment.get(source)
        if not isinstance(bucket, dict):
            continue
        for value in parse_values(bucket.get(field)):
            out.append({
                'raw_genre': value,
                'source': source,
                'source_entity_id': external_ids.get(source),
                'origin': 'library',
            })
    return out


def collect_cached_candidates(cache, row: dict, entity_type: str) -> tuple[list[dict], int]:
    out, hits = [], 0
    provider_ids = provider_ids_from_row(row, entity_type)
    for source in ('spotify', 'itunes', 'deezer', 'discogs', 'audiodb'):
        source_id = provider_ids.get(source)
        if not source_id or not cache: continue
        payload = cache.get_entity(source, entity_type, str(source_id))
        if payload is None: continue
        hits += 1
        for value in extract_provider_genres(source, entity_type, payload):
            out.append({'raw_genre': value, 'source': source, 'source_entity_id': str(source_id), 'origin': 'metadata_cache'})
    return out, hits


def propose_genres(existing: list[str], candidates: list[dict], whitelist: list[str], max_genres: int, cache=None, run_cache=None) -> dict:
    original = list(existing or [])
    translated = []
    for candidate in candidates:
        result = translate_genre(candidate['raw_genre'], whitelist, cache, run_cache)
        item = dict(candidate, translated_genre=result.get('matched_genre'), score=result.get('score'), status=result['status'], candidates=result.get('candidates', []), cache_hit=result.get('cache_hit', False))
        translated.append(item)
    existing_norm = {_normalize_for_match(x) for x in original}
    support = {}
    for item in translated:
        if item['status'] == 'accepted' and item['translated_genre']:
            key = _normalize_for_match(item['translated_genre'])
            support.setdefault(key, {'genre': item['translated_genre'], 'sources': set(), 'confidence': 0,
                                     'translation_confidence': 0, 'order': len(support)})
            support[key]['sources'].add(item['source'])
            support[key]['confidence'] = max(support[key]['confidence'], PROVIDER_CONFIDENCE.get(item['source'], .60))
            support[key]['translation_confidence'] = max(support[key]['translation_confidence'], item.get('score') or 0)
    additions = []
    omitted = []
    ranked = sorted(support.values(), key=lambda x: (-len(x['sources']), -x['confidence'],
        -x['translation_confidence'], x['order'], _normalize_for_match(x['genre'])))
    if len(original) > max_genres:
        omitted = [x['genre'] for x in ranked if _normalize_for_match(x['genre']) not in existing_norm]
    else:
        for item in ranked:
            if _normalize_for_match(item['genre']) in existing_norm: continue
            if len(original) + len(additions) < max_genres: additions.append(item)
            else: omitted.append(item['genre'])
    return {'original_genres': original, 'added_genres': [x['genre'] for x in additions],
            'proposed_genres': original + [x['genre'] for x in additions], 'omitted_due_to_cap': omitted,
            'ambiguous_genres': [x for x in translated if x['status'] == 'ambiguous'],
            'rejected_genres': [x['raw_genre'] for x in translated if x['status'] == 'rejected'],
            'sources': {x['genre']: sorted(x['sources']) for x in additions}, 'translations': translated}
