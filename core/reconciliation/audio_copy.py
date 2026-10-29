"""Explicit album-scoped permission to use a video recording's audio identity."""

from core.reconciliation.catalogue import connection


def save_choice(db, album_id, source, release_id, allowed):
    with connection(db) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_audio_copy_releases (
            album_id TEXT PRIMARY KEY, source TEXT NOT NULL, release_id TEXT NOT NULL)''')
        conn.execute('DELETE FROM reconcile_audio_copy_releases WHERE album_id=?', (str(album_id),))
        if allowed:
            conn.execute('INSERT INTO reconcile_audio_copy_releases VALUES (?,?,?)',
                         (str(album_id), source, release_id))
        conn.commit()


def load_choices(db):
    with connection(db) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_audio_copy_releases'").fetchone():
            return {}
        return {str(album): (source, release) for album, source, release in
                conn.execute('SELECT album_id,source,release_id FROM reconcile_audio_copy_releases')}
