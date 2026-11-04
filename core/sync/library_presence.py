"""are the library tracks a mirror's last sync matched still there? (#1417)

a mirror sync skips when its track list is unchanged and every track matched
last time. that fingerprint never looked at the library, so deleting a matched
file changed nothing it checked: every later sync said "unchanged", nothing was
re-matched or wishlisted, and only deleting and recreating the playlist got the
song back. the skip now also needs every matched library track to still exist.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Iterable, Set

from utils.logging_config import get_logger

logger = get_logger("sync.library_presence")

_CHUNK = 450   # two IN lists plus two source binds stay under SQLite's oldest limit


def _extra(row: Any) -> dict:
    raw = (row or {}).get('extra_data')
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def existing_track_ids(db: Any, ids: Iterable[str]) -> Set[str]:
    """The active server's ids with a live file in the selected library.

    the id a sync records is the media server's track id (that is what the
    sync matches against), so it is looked up through the server mappings and
    the track row's own server id. Another server can reuse the same id, and
    another library can own a different copy of the same catalogue track;
    neither proves that this sync's matched file is still there."""
    from core.library2.sql_util import owner_clause
    from core.settings import config_manager

    server_source = config_manager.get_active_media_server()
    if not server_source:
        return set()
    wanted = sorted({str(i) for i in ids if i not in (None, '')})
    found: Set[str] = set()
    live = ("EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id = {track}"
            " AND COALESCE(f.file_state, 'active') = 'active'"
            " AND f.path IS NOT NULL AND TRIM(f.path) <> ''"
            f"{owner_clause(column='f.owner_profile_id')})")
    with db._get_connection() as conn:
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start:start + _CHUNK]
            marks = ','.join('?' * len(chunk))
            for row in conn.execute(
                    f"SELECT m.server_id FROM lib2_media_server_mappings m"
                    f" WHERE m.entity_type = 'track' AND m.server_source = ?"
                    f" AND m.server_id IN ({marks})"
                    f" AND {live.format(track='m.entity_id')}"
                    f" UNION SELECT t.server_id FROM lib2_tracks t"
                    f" WHERE t.server_source = ? AND t.server_id IN ({marks})"
                    f" AND {live.format(track='t.id')}",
                    [server_source, *chunk, server_source, *chunk]):
                found.add(str(row[0]))
    return found


def matched_tracks_still_present(db: Any, mirror_rows: Iterable[dict]) -> bool:
    """True only when every track the last sync matched is provably still in the
    library. a matched row with no recorded id (synced before ids were kept)
    can't be checked, so it counts as not proven and the sync runs once."""
    ids = []
    for row in mirror_rows:
        extra = _extra(row)
        if not extra.get('in_library'):
            continue
        lib_id = extra.get('library_track_id')
        if not lib_id:
            return False
        ids.append(str(lib_id))
    if not ids:
        return True
    try:
        present = existing_track_ids(db, ids)
    except Exception as e:  # noqa: BLE001 - unsure means sync, never skip
        logger.debug("library presence check failed: %s", e)
        return False
    missing = set(ids) - present
    if missing:
        logger.info("[Sync] %d matched track(s) left the library since the last sync", len(missing))
        return False
    return True


def refresh_history_library_presence(db: Any, entry: dict, server_source: str) -> None:
    """Add today's scoped ownership/recognition to a run without changing its facts.

    Fetch candidates in batches, rather than re-searching the catalogue for
    every historic miss. Provider IDs or recorded server IDs must resolve to
    one owned row; name fallbacks retain strict recording/version identity.
    """
    from core.library_scope import library_scope_for_profile
    from core.library2.sql_util import owner_clause, separated
    from core.downloads.atomic_album_publish import contains_staging_segment
    from core.wishlist.library_match import _strict_identity_matches

    details = [row for row in (entry.get('track_results') or []) if isinstance(row, dict)]
    if not details:
        return
    scope = separated(library_scope_for_profile(entry.get('profile_id')))
    owner = owner_clause(scope=scope, column='f.owner_profile_id')
    by_provider, by_server, by_title = {}, {}, {}
    providers = ('spotify', 'deezer', 'itunes', 'tidal', 'qobuz')
    with db._get_connection() as conn:
        for start in range(0, len(details), 100):
            chunk = details[start:start + 100]
            ids = sorted({str(t['source_track_id']) for t in chunk if t.get('source_track_id')})
            titles = sorted({str(t['name']).lower() for t in chunk if t.get('name')})
            server_ids = sorted({str(t['matched_track']['id']) for t in chunk
                                 if isinstance(t.get('matched_track'), dict) and t['matched_track'].get('id')})
            # the candidates, each through its own index. one OR over these and
            # the computed current_server_id could use none of them and scanned
            # every track: 4.9s to open a 75-track run's details (Oct 31 2026).
            # the id set is a superset of what the OR matched; the keying below
            # still reads each row's own values
            lookups, lookup_params = [], []
            if ids:
                marks = ','.join('?' for _ in ids)
                lookups.append(f'SELECT id FROM lib2_tracks WHERE spotify_id IN ({marks})')
                lookup_params.extend(ids)
                for provider in providers:
                    expression = f"TRIM(CAST(json_extract(external_ids,'$.{provider}') AS TEXT))"
                    lookups.append(f"SELECT id FROM lib2_tracks WHERE {expression} IN ({marks})")
                    lookup_params.extend(ids)
            if titles:
                lookups.append(f"SELECT id FROM lib2_tracks WHERE LOWER(title) IN "
                               f"({','.join('?' for _ in titles)})")
                lookup_params.extend(titles)
            if server_ids:
                marks = ','.join('?' for _ in server_ids)
                lookups.append(f"SELECT entity_id FROM lib2_media_server_mappings WHERE "
                               f"server_source=? AND entity_type='track' AND server_id IN ({marks})")
                lookup_params.extend([server_source, *server_ids])
                lookups.append(f"SELECT id FROM lib2_tracks WHERE server_source=? AND server_id IN ({marks})")
                lookup_params.extend([server_source, *server_ids])
            if not lookups:
                continue
            params = [server_source, server_source, server_source, *lookup_params]
            rows = conn.execute(f"""
                SELECT t.*, COALESCE(NULLIF(t.track_artist,''), a.name) AS artist_name,
                       al.title AS album_title, f.path AS file_path,
                       COALESCE(m.server_id, CASE WHEN t.server_source=? AND f.owner_profile_id IS NULL
                           AND NOT EXISTS (SELECT 1 FROM lib2_media_server_mappings other_m
                               WHERE other_m.entity_type='track' AND other_m.entity_id=t.id
                                 AND other_m.server_source=?) THEN t.server_id END) AS current_server_id
                  FROM lib2_tracks t
                  JOIN lib2_albums al ON al.id=t.album_id
             LEFT JOIN lib2_artists a ON a.id=al.primary_artist_id
                  JOIN lib2_track_files f ON f.track_id=t.id
                       AND COALESCE(f.file_state,'active')='active'
                       AND f.path IS NOT NULL AND TRIM(f.path)<>''{owner}
             LEFT JOIN lib2_media_server_mappings m ON m.entity_type='track'
                       AND m.entity_id=t.id AND m.server_source=?
                       AND m.match_status='recognized'
                       AND m.server_library_id=CASE WHEN f.owner_profile_id IS NULL
                           THEN '' ELSE 'own:' || f.owner_profile_id END
                 WHERE t.id IN ({' UNION '.join(lookups)})
            """, params)
            for raw in rows:
                row = dict(raw)
                if contains_staging_segment(row['file_path']):
                    continue
                external = json.loads(row.get('external_ids') or '{}')
                for provider, value in {('spotify', str(row.get('spotify_id') or '')),
                                        *((p, str(external.get(p) or '')) for p in providers)}:
                    if value:
                        by_provider.setdefault((provider, value), {})[row['id']] = row
                if row['current_server_id']:
                    by_server.setdefault(str(row['current_server_id']), {})[row['id']] = row
                by_title.setdefault(str(row['title']).lower(), {})[row['id']] = row

    for detail in details:
        source_id = str(detail.get('source_track_id') or '')
        provider = detail.get('provider') or entry.get('source')
        if provider in providers:
            candidates = by_provider.get((provider, source_id))
        else:
            candidates = {key: row for p in providers
                          for key, row in by_provider.get((p, source_id), {}).items()} or None
            if candidates and len(candidates) == 1:
                row = next(iter(candidates.values()))
                if not _strict_identity_matches(SimpleNamespace(**row), detail.get('name') or '',
                                                detail.get('artist') or '', detail.get('album'), False):
                    candidates = None
        if not candidates:
            matched = detail.get('matched_track') or {}
            candidates = by_server.get(str(matched.get('id') or ''))
        if candidates is None:
            candidates = {
                key: row for key, row in by_title.get(str(detail.get('name') or '').lower(), {}).items()
                if _strict_identity_matches(SimpleNamespace(**row), detail.get('name') or '',
                                            detail.get('artist') or '', detail.get('album'), False)
            }
        row = next(iter(candidates.values())) if candidates and len(candidates) == 1 else None
        detail['current_library_match'] = ({'catalogue_id': row['id'],
                                            'server_id': row['current_server_id'],
                                            'server_type': server_source} if row else None)
    entry['current_tracks_in_library'] = sum(bool(t['current_library_match']) for t in details)
    entry['current_tracks_recognized'] = sum(bool((t['current_library_match'] or {}).get('server_id')) for t in details)


__all__ = ['existing_track_ids', 'matched_tracks_still_present', 'refresh_history_library_presence']
