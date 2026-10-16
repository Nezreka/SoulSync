"""Artist-scoped media-server sync using the existing recognition worker."""
from contextlib import closing
from collections import defaultdict


def sync_artist(database, artist_id, media_engine=None, *, worker_factory=None):
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.queries import get_artist
    from core.library2.media_server_sync import scan_library_id
    from core.library_scope import current_library_scope, owner_for_scope
    from core.library.stale_guard import is_implausible_stale_removal

    with closing(database._get_connection()) as conn:
        artist = get_artist(conn, artist_id)
        if artist is None:
            raise ValueError('Artist not found')
        group = resolve_alias_group(conn, artist_id)
        marks = ','.join('?' for _ in group)
        library_id = scan_library_id()
        mappings = conn.execute(
            f"SELECT server_source, server_id FROM lib2_media_server_mappings "
            f"WHERE entity_type='artist' AND entity_id IN ({marks}) AND server_library_id=?",
            (*group, library_id)).fetchall()
    result = dict(success=True, artist_name=artist['name'], name_updated=False,
                  new_albums=0, new_tracks=0, stale_removed=0,
                  empty_albums_removed=0, removal_skipped=not bool(mappings))
    sources = defaultdict(list)
    for source, server_id in mappings:
        sources[source].append(server_id)
    if media_engine is None:
        result['removal_skipped'] = True
        return result
    if worker_factory is None:
        from core.database_update_worker import DatabaseUpdateWorker
        worker_factory = DatabaseUpdateWorker
    for source, server_ids in sources.items():
        seen = set()
        confirmed = True
        try:
            client = media_engine.client(source)
            if client is None:
                result['removal_skipped'] = True
                continue
            with closing(database._get_connection()) as conn:
                known = {str(row[0]) for row in conn.execute(
                    f"SELECT m.server_id FROM lib2_media_server_mappings m "
                    f"JOIN lib2_tracks t ON t.id=m.entity_id JOIN lib2_albums al ON al.id=t.album_id "
                    f"WHERE m.entity_type='track' AND m.server_source=? AND m.server_library_id=? "
                    f"AND al.primary_artist_id IN ({marks})", (source, library_id, *group))}
            worker = worker_factory(media_client=client, database_path=str(database.database_path),
                                    full_refresh=False, server_type=source, force_sequential=True,
                                    owner_profile_id=owner_for_scope(current_library_scope()))
            worker.database = database
            for server_id in server_ids:
                remote = (client.server.fetchItem(int(server_id)) if source == 'plex'
                          else client.get_artist_by_id(str(server_id)))
                if remote is None:
                    confirmed = False
                    continue
                ok, _details, albums, tracks = worker._process_artist_with_content(
                    remote, skip_existing_tracks=True, seen_track_ids=seen)
                confirmed = confirmed and bool(ok)
                result['new_albums'] += albums
                result['new_tracks'] += tracks
            confirmed = confirmed and not (worker._unverified_album_ids or worker._unverified_artist_ids)
            stale = known - {str(value) for value in seen}
            if not confirmed or is_implausible_stale_removal(len(stale), len(known)):
                result['removal_skipped'] = True
            elif stale:
                result['stale_removed'] += database.delete_stale_tracks(
                    stale, source, owner_profile_id=owner_for_scope(current_library_scope()))
        except Exception:
            # A failed or partial listing never authorizes stale removal.
            from utils.logging_config import get_logger
            get_logger('library2.artist_sync').exception('Artist media-server sync failed')
            result['removal_skipped'] = True
    with closing(database._get_connection()) as conn:
        refreshed = get_artist(conn, artist_id)
        if refreshed:
            result['name_updated'] = refreshed['name'] != result['artist_name']
            result['artist_name'] = refreshed['name']
    return result
