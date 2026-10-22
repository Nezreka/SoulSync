"""one artist split in two on unicode hyphens (#1797).

musicbrainz spells alt-J with a U+2010 hyphen, apple/deezer with ascii '-'.
the standalone scanner keyed artists on lower().strip() and the post-scan
merge grouped on the exact name, so files tagged from either source landed
under two artists. both now fold dash variants for the grouping only: the
stored display name is untouched, and a variant spelling won't merge when a
provider id disagrees.
"""

import threading

import pytest

import core.library_scope as scope_mod
import core.soulsync_client as ss_mod
from core.soulsync_client import SoulSyncClient
from core.text.normalize import fold_dashes
from database.music_database import MusicDatabase

UNICODE = 'alt‐J'
ASCII = 'alt-J'


def test_fold_dashes_only_touches_hyphens():
    assert fold_dashes(UNICODE) == ASCII
    assert fold_dashes('a‑b‒c–d−e﹣f－g') == 'a-b-c-d-e-f-g'
    assert fold_dashes('Sigur Rós — live') == 'Sigur Rós — live'
    assert fold_dashes('') == '' and fold_dashes(None) == ''


# ── scanner ──────────────────────────────────────────────────────────────────

def _tags(album, artist):
    return {'title': 't', 'artist': artist, 'album_artist': artist, 'album': album,
            'track_number': 1, 'disc_number': 1, 'year': '2012', 'genre': '',
            'duration_ms': 1000, 'bitrate': 320}


def test_the_scan_files_both_spellings_under_one_artist(tmp_path, monkeypatch):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'a' / '1.flac').write_bytes(b'')
    (tmp_path / 'b' / '1.flac').write_bytes(b'')
    tags = {str(tmp_path / 'a' / '1.flac'): _tags('An Awesome Wave', UNICODE),
            str(tmp_path / 'b' / '1.flac'): _tags('This Is All Yours', ASCII)}
    monkeypatch.setattr(ss_mod, '_read_tags', lambda p: tags[p])

    client = SoulSyncClient.__new__(SoulSyncClient)
    client._transfer_path = str(tmp_path)
    client._progress_callback = None
    client._scan_lock = threading.Lock()

    artists = client._scan_transfer()
    assert len(artists) == 1
    assert artists[0].title in (UNICODE, ASCII)    # a real spelling, not the key
    assert sorted(a.title for a in artists[0].albums()) == ['An Awesome Wave', 'This Is All Yours']


# ── post-scan merge ──────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'lib.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


def _artist(db, aid, name, **cols):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'soulsync')",
                     (aid, name))
        conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'soulsync')",
                     (f'{aid}-al', aid, f'album {aid}'))
        for col, val in cols.items():
            conn.execute(f"UPDATE artists SET {col} = ? WHERE id = ?", (val, aid))
        conn.commit()


def _artists(db):
    with db._get_connection() as conn:
        return {r['id']: r['name'] for r in conn.execute("SELECT id, name FROM artists")}


def _album_owner(db, album_id):
    with db._get_connection() as conn:
        return conn.execute("SELECT artist_id FROM albums WHERE id = ?", (album_id,)).fetchone()[0]


def test_the_merge_joins_dash_variants_and_keeps_the_keepers_name(db):
    _artist(db, 'mb', UNICODE, musicbrainz_id='fc7bbf00')
    _artist(db, 'dz', ASCII, deezer_id='1606315')
    _artist(db, 'other', 'Alt-Country')

    result = db.merge_duplicate_artists()

    assert result['artists_merged'] == 1
    survivors = _artists(db)
    assert set(survivors) == {'mb', 'other'} or set(survivors) == {'dz', 'other'}
    keeper = next(k for k in survivors if k != 'other')
    assert survivors[keeper] == {'mb': UNICODE, 'dz': ASCII}[keeper]
    assert _album_owner(db, 'mb-al') == keeper and _album_owner(db, 'dz-al') == keeper
    with db._get_connection() as conn:
        row = conn.execute("SELECT musicbrainz_id, deezer_id FROM artists WHERE id = ?",
                           (keeper,)).fetchone()
    assert (row['musicbrainz_id'], row['deezer_id']) == ('fc7bbf00', '1606315')


def test_a_variant_with_a_conflicting_provider_id_stays_separate(db):
    _artist(db, 'one', UNICODE, musicbrainz_id='mbid-one')
    _artist(db, 'two', ASCII, musicbrainz_id='mbid-two')

    assert db.merge_duplicate_artists()['artists_merged'] == 0
    assert _artists(db) == {'one': UNICODE, 'two': ASCII}


def test_exact_name_duplicates_merge_as_before(db):
    """the provider guard is for variant spellings only; exact-name
    duplicates keep the merge they always had."""
    _artist(db, 'one', ASCII, musicbrainz_id='mbid-one')
    _artist(db, 'two', ASCII, musicbrainz_id='mbid-two')

    assert db.merge_duplicate_artists()['artists_merged'] == 1
    assert len(_artists(db)) == 1
