"""Final naming approved by an album review, shared by preview and execution."""
from __future__ import annotations

import os


def organization_context(db, album_id, details):
    conn = db._get_connection()
    try:
        album = conn.execute('SELECT title FROM albums WHERE id=?', (str(album_id),)).fetchone()
        filenames = {str(row[0]): os.path.basename(row[1] or '') for row in conn.execute(
            'SELECT id, file_path FROM tracks WHERE album_id=?', (str(album_id),))}
    finally:
        conn.close()
    title = album[0] if album else ''
    for item in details:
        title = item.get('db_album_title') or title
        change = (item.get('changes') or {}).get('filename') or {}
        if change.get('new') and item.get('track_id') is not None:
            name = str(change['new'])
            if name != os.path.basename(name) or name in ('.', '..'):
                raise ValueError('The reviewed filename must be a filename, not a path')
            filenames[str(item['track_id'])] = name
    return {'album_title': title, 'filenames': filenames}
