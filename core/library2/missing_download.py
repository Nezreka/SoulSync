"""Select a one-off missing download; acquisition remains the shared pipeline."""


def selected_track_ids(database, conn, config_manager, entity, entity_id, album_ids=None):
    from core.library2.queries import get_artist
    from core.library2.wanted import entity_track_ids
    from core.library2.completeness import load_album_catalogue

    entity = entity.rstrip('s')
    if entity == 'artist':
        artist = get_artist(conn, entity_id)
        allowed = {a['id'] for key in ('albums', 'eps', 'singles') for a in (artist or {}).get(key, [])}
    elif entity == 'album':
        allowed = {entity_id}
    else:
        raise ValueError('Missing downloads require an artist or album')
    selected = set(album_ids) if album_ids is not None else allowed
    if not selected or not selected <= allowed:
        raise ValueError('Select releases belonging to this artist or album')
    for aid in selected:
        row = conn.execute('SELECT expected_track_count, (SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?) '
                           'FROM lib2_albums WHERE id=?', (aid, aid)).fetchone()
        if row and (not row[1] or (row[0] and row[0] > row[1])):
            load_album_catalogue(database, config_manager, conn, aid, inherit_monitoring=False)
    scope = set(entity_track_ids(conn, entity, entity_id))
    result = set()
    albums = sorted(selected)
    for start in range(0, len(albums), 500):
        chunk = albums[start:start + 500]
        result.update(row[0] for row in conn.execute(
            f'SELECT id FROM lib2_tracks WHERE album_id IN ({",".join("?" for _ in chunk)})', chunk))
    return sorted(scope & result)
