"""Regression for #977 — the discography backfill only backfills artists you
actually OWN music by. The catalogue also carries rows for featured/guest
artists, alias rows and artists you only wishlisted; without an ownership filter
the backfill pulled their entire discography ("artists which are not in my library").
"""
import types

from database.music_database import MusicDatabase
from core.repair_jobs.discography_backfill import DiscographyBackfillJob


def _seed(db):
    from tests import lib2_seed
    with db._get_connection() as conn:
        # Owned artist: a release of their own on disk.
        lib2_seed.track(conn, 'Owned Artist', 'Alb', 'T')
        # Artist known only through a release with no file yet: not in library.
        lib2_seed.track(conn, 'Wishlist Only Artist', 'Alb2', 'T2', owned=False)
        # A guest on the owned artist's track: credited, but no release of their own.
        lib2_seed.track(conn, 'Owned Artist', 'Alb', 'T3', credit='Feat Guest')
        # An alias row of the owned artist: its canonical row is scanned instead.
        alias = lib2_seed.track(conn, 'Owned Alias', 'Alias Alb', 'T4')
        canonical = conn.execute("SELECT id FROM lib2_artists WHERE name='Owned Artist'").fetchone()[0]
        conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE name='Owned Alias'", (canonical,))
        assert alias
        conn.commit()


def test_backfill_scope_excludes_non_library_artists(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db)
    job = DiscographyBackfillJob()
    names = {a['name'] for a in job._get_library_artists(types.SimpleNamespace(db=db))}

    assert names == {'Owned Artist'}
    # 'Wishlist Only Artist': no file of theirs on disk, not in the library
    # 'Feat Guest': a guest credit, not a release of their own (the #977 fix)
    # 'Owned Alias': an alias row; the canonical artist is the one scanned


def test_min_album_tracks_needs_that_many_on_one_album_or_the_watchlist(tmp_path):
    from tests import lib2_seed
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        for n in range(3):
            lib2_seed.track(conn, 'Collected', 'Full Album', f'Song {n}')
        lib2_seed.track(conn, 'Playlist Grab', 'Their Album', 'One Song')
        lib2_seed.track(conn, 'Watched', 'Their Album', 'One Song')
        conn.execute("UPDATE lib2_artists SET monitored=1 WHERE name='Watched'")
        conn.commit()
    job = DiscographyBackfillJob()
    names = {a['name'] for a in job._get_library_artists(types.SimpleNamespace(db=db), min_album_tracks=3)}
    assert names == {'Collected', 'Watched'}


def test_provider_ids_come_from_the_catalogue(tmp_path):
    from tests import lib2_seed
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Owned Artist', 'Alb', 'T')
        conn.execute("""UPDATE lib2_artists SET spotify_id='sp1',
                        external_ids='{"itunes": "it1", "deezer": 42}' WHERE name='Owned Artist'""")
        conn.commit()
    artist = DiscographyBackfillJob()._get_library_artists(types.SimpleNamespace(db=db))[0]
    assert (artist['spotify_artist_id'], artist['itunes_artist_id'], artist['deezer_id']) == ('sp1', 'it1', '42')


def test_findings_route_to_the_one_profile_whose_library_holds_the_artist(tmp_path):
    from tests import lib2_seed
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Kid Artist', 'Alb', 'T')
        lib2_seed.track(conn, 'Shared Artist', 'Alb', 'T')
        conn.execute("""UPDATE lib2_track_files SET owner_profile_id=3 WHERE track_id IN
                        (SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
                          JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE ar.name='Kid Artist')""")
        kid, shared = (conn.execute("SELECT id FROM lib2_artists WHERE name=?", (n,)).fetchone()[0]
                       for n in ('Kid Artist', 'Shared Artist'))
        conn.commit()
    assert DiscographyBackfillJob._artist_owner(db, kid) == 3
    assert DiscographyBackfillJob._artist_owner(db, shared) is None
