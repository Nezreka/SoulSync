"""Remember a successful native placement without freezing provider metadata.

Automatic catalogue refreshes do not move an already placed album again.
Naming settings, explicit pins, manual metadata and changes to its file set
invalidate the memory. Interactive reorganize still previews the current
catalogue; the periodic job can explicitly recheck placed albums.
"""
import hashlib
import json


def _signature(conn, album_id, naming):
    album = conn.execute('SELECT primary_artist_id, canonical_locked, canonical_source, canonical_album_id FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
    if album is None:
        return None
    tracks = [r[0] for r in conn.execute('SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY id', (album_id,))]
    artists = {album[0]}
    artists.update(r[0] for r in conn.execute(
        'SELECT DISTINCT ta.artist_id FROM lib2_track_artists ta JOIN lib2_tracks t ON t.id=ta.track_id WHERE t.album_id=?', (album_id,)))
    # One indexed override read for the album, its tracks and their artists;
    # never one database round trip per track on a large release.
    artist_ids = sorted(a for a in artists if a is not None)
    marks = ','.join('?' for _ in artist_ids) or 'NULL'
    overrides = [tuple(r) for r in conn.execute(
        "SELECT entity_type,entity_id,field_name,value_json FROM lib2_metadata_overrides WHERE "
        "(entity_type='release_group' AND entity_id=?) OR "
        "(entity_type='track' AND entity_id IN (SELECT id FROM lib2_tracks WHERE album_id=?)) OR "
        f"(entity_type='artist' AND entity_id IN ({marks})) ORDER BY entity_type,entity_id,field_name",
        (album_id, album_id, *artist_ids))]
    files = [tuple(r) for r in conn.execute(
        'SELECT f.id,f.track_id,f.path,f.owner_profile_id,f.file_state FROM lib2_track_files f '
        'JOIN lib2_tracks t ON t.id=f.track_id WHERE t.album_id=? ORDER BY f.id', (album_id,))]
    pin = list(album[2:]) if album[1] else None
    payload = [naming, pin, tracks, files, overrides]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def record_placement(conn, album_id, naming):
    signature = _signature(conn, album_id, naming)
    if signature:
        conn.execute('UPDATE lib2_albums SET reorganize_placement=? WHERE id=?', (signature, album_id))
    return bool(signature)


def placement_settled(conn, album_id, naming):
    row = conn.execute('SELECT reorganize_placement FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
    return bool(row and row[0] and row[0] == _signature(conn, album_id, naming))
