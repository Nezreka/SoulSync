"""Adapt a download context to the existing album-page catalogue loader."""
from __future__ import annotations

from contextlib import closing
from datetime import date

from core.library2.autolink import find_or_create_album, find_or_create_artist
from core.library2.release_kind import confirm_release_kind


def _ensure_album(database, context, album, source):
    from core.imports.context import get_import_context_artist
    artist = get_import_context_artist(context)
    artists = album.get('artists') or [artist]
    first = artists[0] if isinstance(artists, list) and artists else artist
    artist = first if isinstance(first, dict) else {'name': str(first)}
    title = str(album.get('name') or album.get('title') or '').strip()
    if not title or not artist.get('name'):
        raise ValueError('Album catalogue requires an album and artist')
    with closing(database._get_connection()) as conn:
        aid = find_or_create_artist(conn, artist['name'], spotify_id=artist.get('id'), source=source)
        old_max = conn.execute('SELECT COALESCE(MAX(id),0) FROM lib2_albums').fetchone()[0]
        album_id = find_or_create_album(conn, aid, title, album_type=album.get('album_type') or 'album',
                                       spotify_album_id=album.get('id'), source=source, monitored=0)
        if album_id > old_max:
            conn.execute("UPDATE lib2_albums SET origin='discography' WHERE id=?", (album_id,))
        row = conn.execute('SELECT release_date, canonical_locked, canonical_source, canonical_album_id '
                           'FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
        existing = row['release_date']
        incoming = album.get('release_date')
        pinned_elsewhere = row['canonical_locked'] and (
            str(row['canonical_source'] or '').lower() != str(source).lower()
            or str(row['canonical_album_id'] or '') != str(album.get('id') or ''))
        # Fill known components of the same date, never correct an existing
        # day or borrow another edition's date across an explicit pin.
        release_date = existing or incoming
        if existing and incoming and not pinned_elsewhere:
            old_date, new_date = str(existing).strip(), str(incoming).strip()
            if len(old_date) in (4, 7) and new_date.startswith(old_date + '-'):
                try:
                    date.fromisoformat(new_date + '-01' if len(new_date) == 7 else new_date)
                except ValueError:
                    release_date = existing  # malformed provider dates cannot refine the catalogue
                else:
                    release_date = new_date
        conn.execute('UPDATE lib2_albums SET release_date=?, '
                     'expected_track_count=COALESCE(expected_track_count, ?) WHERE id=?',
                     (release_date, album.get('total_tracks'), album_id))
        confirm_release_kind(conn, album_id, album.get('album_type'), known=album.get('_album_type_known') is not False)
        conn.commit()
    context['_album_catalogue_id'] = album_id
    return album_id


def persist_album_payload(database, context: dict, payload: dict) -> int:
    """Feed a prefetched provider response through the common album resolver."""
    from core.library2.completeness import load_album_catalogue
    from core.library2.provider_adapters import TracklistTrack, TracklistProviderResult
    from core.settings import config_manager
    album = payload.get('album') or {}
    source = str(payload.get('source') or album.get('source') or '').strip().lower()
    tracks = payload.get('tracks') or []
    if not source or not tracks or not album.get('id'):
        raise ValueError('Album catalogue requires a provider-qualified tracklist')
    normalized = tuple(item for raw in tracks if (item := TracklistTrack.from_item(raw, provider=source)))
    if len(normalized) != len(tracks):
        raise ValueError('Album catalogue contains incomplete track identities')
    album_id = _ensure_album(database, context, album, source)
    complete = payload.get('is_complete', len(normalized) >= int(album.get('total_tracks') or len(normalized))) is True
    result = TracklistProviderResult(source, str(album['id']), normalized, is_complete=complete)
    with closing(database._get_connection()) as conn:
        load_album_catalogue(database, config_manager, conn, album_id,
                             inherit_monitoring=False, provider_result=result, enrich=False)
    return album_id


def hydrate_download_album(context: dict, *, lookup_album=None):
    """Load exactly as the album page does, before search or file processing."""
    from core.imports.context import get_import_context_album, get_import_source
    from database.music_database import get_database
    from core.library2.completeness import load_album_catalogue
    from core.settings import config_manager
    from utils.logging_config import get_logger
    album = get_import_context_album(context)
    artist = context.get('artist') or {}
    name = album.get('name') or album.get('title')
    artist_name = artist.get('name') if isinstance(artist, dict) else str(artist)
    if not name or not artist_name:
        return None
    try:
        database = get_database()
        lookup_key = (get_import_source(context), album.get('id'), name, artist_name)
        payload = context.get('_release_album_payload') if context.get('_release_album_lookup') == lookup_key else None
        if not payload and lookup_album is not None:
            payload = lookup_album(album.get('id') or 'from_sync_modal', artist_name=artist_name,
                                   album_name=name, source_override=get_import_source(context) or None)
        if payload:
            from core.library2.native_enrich import titles_are_same_release
            if not isinstance(payload, dict) or not payload.get('success') or not titles_are_same_release(name, (payload.get('album') or {}).get('name')):
                return None
            persist_album_payload(database, context, payload)
        else:
            album_id = _ensure_album(database, context, album, get_import_source(context))
            with closing(database._get_connection()) as conn:
                load_album_catalogue(database, config_manager, conn, album_id, inherit_monitoring=False)
            payload = _cached_album_payload(database, context)
        if not payload:
            return None
        context['_release_album_payload'] = payload
        context['_release_album_lookup'] = lookup_key
        return payload
    except Exception as exc:
        get_logger('library2.download_catalogue').warning('Album catalogue loading failed for %r: %s', name, exc)
        return None


def _cached_album_payload(database, context):
    """Shape the resolver's normalized snapshot for the existing import pipeline."""
    from core.library2.completeness import _album_tracklist_context, _snapshot_tracks
    from core.library2.provider_snapshots import get_latest_provider_snapshot
    from core.library2.native_enrich import _stored_source_ids
    album_id = context.get('_album_catalogue_id')
    if not album_id:
        return None
    with closing(database._get_connection()) as conn:
        info = _album_tracklist_context(conn, int(album_id))
        if info is None:
            return None
        _, reference, _ = info
        snapshot = get_latest_provider_snapshot(conn, entity_type='album', entity_id=album_id, scope='tracklist')
        tracks = _snapshot_tracks(snapshot, reference)
        if not tracks:
            return None
        row = conn.execute('SELECT * FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
        ar = conn.execute('SELECT * FROM lib2_artists WHERE id=?', (row['primary_artist_id'],)).fetchone()
        provider = snapshot.provider
        artist = {'name': ar['name'], 'id': _stored_source_ids(ar).get(provider)}
        raw_tracks = []
        for track in tracks:
            credits = track.get('artist_credits') or []
            names = [{'name': c.get('name'), 'id': c.get('provider_id')} for c in credits] or [artist]
            raw_tracks.append({**track, 'name': track['title'], 'id': (track.get('external_ids') or {}).get(provider), 'artists': names})
        return {'success': True, 'source': provider, 'is_complete': True, 'album': {'id': snapshot.provider_entity_id, 'name': row['title'],
                'artists': [artist], 'album_type': row['album_type'], '_album_type_known': bool(row['album_type_known']),
                'release_date': row['release_date'], 'total_tracks': len(tracks)}, 'tracks': raw_tracks}
