"""Native guest appearances over shared catalogue and scoped file readers."""
from core.library2.queries import get_artist


def appears_on_payload(conn, artist_id):
    """Guest appearances using the shared alias, metadata and file projection."""
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.queries import get_album
    from core.library2.sql_util import intent_profile_id, monitored_sql
    group = resolve_alias_group(conn, int(artist_id))
    marks = ','.join('?' for _ in group)
    albums = []
    tracks = []
    credited_tracks = {r[0] for r in conn.execute(
        f'SELECT track_id FROM lib2_track_artists WHERE artist_id IN ({marks})', group)}
    credited_albums = {r[0] for r in conn.execute(
        f'SELECT album_id FROM lib2_album_artists WHERE artist_id IN ({marks})', group)}
    # releases with a wanted track of this artist's, wanted as the library
    # counts it (a track, album or artist rule): that is what puts a guest in
    # the library before the file is. the summary's monitored_tracks reads the
    # track's own flag only, and counts everyone's tracks
    wanted_albums = {r[0] for r in conn.execute(
        f"SELECT DISTINCT t.album_id FROM lib2_tracks t "
        f"LEFT JOIN lib2_wanted_tracks w ON w.track_id=t.id AND w.profile_id={intent_profile_id()} "
        f"WHERE (t.id IN (SELECT track_id FROM lib2_track_artists WHERE artist_id IN ({marks})) "
        f"OR t.album_id IN (SELECT album_id FROM lib2_album_artists WHERE artist_id IN ({marks}))) "
        f"AND COALESCE(w.wanted, {monitored_sql('track', 't')})=1", (*group, *group))}
    native = get_artist(conn, int(artist_id))
    if native is None:
        return {'success': True, 'albums': [], 'tracks': []}
    for summary in (*native['albums'], *native['eps'], *native['singles'],
                    *native.get('appears_on', [])):
        # The native summary already applied ownership/recording-link scope.
        # Do not load every provider-only album's tracks just to discard it.
        # A wanted track counts too (see wanted_albums).
        if not summary['tracks_present'] and summary['id'] not in wanted_albums:
            continue
        album = get_album(conn, summary['id'])
        primary = album.get('primary_artist') or {}
        # the cover through soulsync's own artwork endpoint, like every other
        # library card. the stored image_url can be a media server's own path
        # ("/library/metadata/570524/thumb/...") the browser can't load
        cover = f"/api/library/v2/artwork/album/{int(album['id'])}"
        # the provider's own cover, painted while a cold local one is built
        stored = str(album.get('image_url') or '')
        remote = stored if stored.startswith(('http://', 'https://')) else None
        if primary.get('id') in group:
            continue
        whole_album = album['id'] in credited_albums
        rows = []
        for track in album['tracks']:
            file = track.get('file')
            live = bool(file) and track.get('file_status') != 'missing' and file.get('file_state') in (None, 'active')
            # not here yet but wanted: listed, with nothing to play
            if not live and not track.get('monitored'):
                continue
            if not whole_album and track['id'] not in credited_tracks:
                continue
            rows.append({
                **{k: track.get(k) for k in ('id', 'title', 'track_number', 'disc_number', 'duration')},
                'lib2_track_id': track['id'], 'file_path': file['path'] if live else None,
                'wanted': not live,
                'album_id': album['id'], 'album_title': album['title'],
                'album_thumb_url': cover, 'album_thumb_remote_url': remote,
                'year': album['year'],
                'artist_id': primary.get('id'), 'artist_name': primary.get('name'),
                'credits': [a['name'] for a in track.get('artists', [])],
            })
        if whole_album and rows:
            albums.append({'id': album['id'], 'title': album['title'],
                           'thumb_url': cover, 'thumb_remote_url': remote,
                           'year': album['year'],
                           'artist_id': primary.get('id'), 'artist_name': primary.get('name'),
                           'tracks': rows})
        elif rows:
            tracks.extend(rows)
    return {'success': True, 'albums': albums, 'tracks': tracks}
