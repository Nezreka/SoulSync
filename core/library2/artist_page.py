"""Native guest appearances over shared catalogue and scoped file readers."""
from core.library2.queries import get_artist


def appears_on_payload(conn, artist_id):
    """Guest appearances using the shared alias, metadata and file projection."""
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.queries import get_album
    group = resolve_alias_group(conn, int(artist_id))
    marks = ','.join('?' for _ in group)
    albums = []
    tracks = []
    credited_tracks = {r[0] for r in conn.execute(
        f'SELECT track_id FROM lib2_track_artists WHERE artist_id IN ({marks})', group)}
    credited_albums = {r[0] for r in conn.execute(
        f'SELECT album_id FROM lib2_album_artists WHERE artist_id IN ({marks})', group)}
    native = get_artist(conn, int(artist_id))
    if native is None:
        return {'success': True, 'albums': [], 'tracks': []}
    for summary in (*native['albums'], *native['eps'], *native['singles']):
        # The native summary already applied ownership/recording-link scope.
        # Do not load every provider-only album's tracks just to discard it.
        if not summary['tracks_present']:
            continue
        album = get_album(conn, summary['id'])
        primary = album.get('primary_artist') or {}
        if primary.get('id') in group:
            continue
        whole_album = album['id'] in credited_albums
        rows = []
        for track in album['tracks']:
            file = track.get('file')
            if not file or track.get('file_status') == 'missing' or file.get('file_state') not in (None, 'active'):
                continue
            if not whole_album and track['id'] not in credited_tracks:
                continue
            rows.append({
                **{k: track.get(k) for k in ('id', 'title', 'track_number', 'disc_number', 'duration')},
                'lib2_track_id': track['id'], 'file_path': file['path'],
                'album_id': album['id'], 'album_title': album['title'],
                'album_thumb_url': album['image_url'], 'year': album['year'],
                'artist_id': primary.get('id'), 'artist_name': primary.get('name'),
                'credits': [a['name'] for a in track.get('artists', [])],
            })
        if whole_album and rows:
            albums.append({'id': album['id'], 'title': album['title'],
                           'thumb_url': album['image_url'], 'year': album['year'],
                           'artist_id': primary.get('id'), 'artist_name': primary.get('name'),
                           'tracks': rows})
        elif rows:
            tracks.extend(rows)
    return {'success': True, 'albums': albums, 'tracks': tracks}
