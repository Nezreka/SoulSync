"""Split Artists — a catalogue healer (#2150, #2131, #2136).

The job reads library artists, never file tags, and decides each name with
the one credit kernel: the provider credits stored for its tracks (asking the
credit fetcher when there are none), the album's own artist as a part, the
library's identified artists and the user's Keep Whole Names. The fix heals
the catalogue through the one combined-artist heal and asks Re-tag and
Reorganize to check the affected files; it never writes a tag itself.
"""

from __future__ import annotations

import pytest

import core.library2.provider_credits as pc
from core.repair_jobs.base import JobContext
from core.repair_jobs.comma_artist_splitter import (
    CommaArtistSplitterJob,
    _splits_only_at,
    custom_split_parts,
    normalize_artist_name,
    split_artist_parts,
)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests import lib2_seed

KDA = 'K/DA; League Of Legends; Mimi Webb; Sophie Powers'


# ── helpers ──────────────────────────────────────────────────────────────────

class _Cfg:
    """config stand-in: dotted get/set over one dict"""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _settings(**values):
    return _Cfg({'repair.jobs.comma_artist_splitter.settings': values})


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _album_by(db, artist, *titles, album='Album', **artist_cols):
    """an album by ``artist`` with one owned track per title"""
    with db._get_connection() as conn:
        ids = [lib2_seed.track(conn, artist, album, title) for title in titles]
        if artist_cols:
            lib2_seed.artist(conn, artist, **artist_cols)
        conn.commit()
    return ids


def _guest_on(db, album_artist, guest, title='Song', album='Album'):
    """a track on ``album_artist``'s album, credited to ``guest``"""
    with db._get_connection() as conn:
        track = lib2_seed.track(conn, album_artist, album, title, credit=guest)
        conn.commit()
    return track


def _identified(db, *names):
    with db._get_connection() as conn:
        for index, name in enumerate(names):
            lib2_seed.artist(conn, name, spotify_id=f'sp{index}{len(name)}')
        conn.commit()


def _credits(db, track_id, names, source='spotify'):
    """what the credit fetcher stores for a track"""
    with db._get_connection() as conn:
        for position, name in enumerate(names):
            conn.execute("INSERT INTO lib2_provider_credits(entity_type, entity_id, source, "
                         "position, name, source_artist_id) VALUES('track', ?, ?, ?, ?, ?)",
                         (track_id, source, position, name, f'{source}-{position}'))
        conn.commit()


def _artist_id(db, name):
    with db._get_connection() as conn:
        row = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def _scan(db, cfg=None, scope=None):
    findings = []
    ctx = JobContext(db=db, transfer_folder='/tmp', config_manager=cfg or _Cfg(),
                     create_finding=lambda **kw: findings.append(kw) or True)
    if scope is not None:
        ctx.scope = scope
    result = CommaArtistSplitterJob().scan(ctx)
    return result, findings


@pytest.fixture
def no_fetch(monkeypatch):
    """the credit fetcher, recorded: it finds nothing"""
    calls = []
    monkeypatch.setattr(pc, 'ensure_track_credits',
                        lambda database, track_id, services=None, **kw: calls.append(track_id) or False)
    return calls


def _worker(db, cfg=None):
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._config_manager = cfg or _Cfg()
    worker.transfer_folder = '/tmp'
    worker.asked = []
    worker.request_projection = lambda track_ids, **kw: worker.asked.append(
        (sorted(track_ids), kw)) or {'files': len(track_ids), 'queued': [
            k for k in ('retag', 'reorganize') if kw.get(k)]}
    return worker


# ── unit: name helpers ───────────────────────────────────────────────────────

def test_split_artist_parts():
    assert split_artist_parts('Camellia, Toby Fox') == ['Camellia', 'Toby Fox']
    assert split_artist_parts('A; B; C') == ['A', 'B', 'C']
    assert split_artist_parts('A & B') == ['A', 'B']
    assert split_artist_parts('A, B & C / D') == ['A', 'B', 'C', 'D']
    assert split_artist_parts('A & B', [',']) == ['A & B']
    assert split_artist_parts('') == []


def test_normalize_artist_name():
    assert normalize_artist_name('Tyler,The Creator') == normalize_artist_name('Tyler, The Creator')
    assert normalize_artist_name('  Camellia ,  TOBY FOX ') == 'camellia, toby fox'


def test_splits_only_at_enabled_separators():
    assert _splits_only_at('Camellia, Toby Fox', ['Camellia', 'Toby Fox'], [','])
    assert not _splits_only_at('Camellia & Toby Fox', ['Camellia', 'Toby Fox'], [','])
    # a guest marker always separates artists: the import hands "A feat. B" over
    assert _splits_only_at('A feat. B', ['A', 'B'], [',', ';', '/', '&'])
    assert not _splits_only_at('NovaVale', ['Nova', 'Vale'], [','])


def test_custom_split_parts_only_regroups_the_names_own_pieces():
    parts, joined = custom_split_parts(KDA, 'K/DA; League Of Legends; Mimi Webb; Sophie Powers')
    assert parts == ['K/DA', 'League Of Legends', 'Mimi Webb', 'Sophie Powers']
    assert joined == ['K/DA']
    parts, joined = custom_split_parts('Above & Beyond, Mat Zo', 'above &beyond; Mat Zo')
    assert parts == ['Above & Beyond', 'Mat Zo'] and joined == ['Above & Beyond']
    for bad in ('K/DA; Somebody Else; Mimi Webb; Sophie Powers',   # invents a name
                'K/DA; League Of Legends',                            # drops names
                'K/DA League Of Legends; Mimi Webb; Sophie Powers',   # runs over a ";"
                ''):
        with pytest.raises(ValueError):
            custom_split_parts(KDA, bad)


# ── scan: the catalogue decides, never the file ─────────────────────────────

def test_stored_provider_credits_split_a_combined_artist(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One', 'Two')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    result, findings = _scan(db)
    assert result.findings_created == 1
    finding = findings[0]
    combined = _artist_id(db, 'Camellia, Toby Fox')
    assert finding['entity_type'] == 'artist' and finding['entity_id'] == f'lib2:{combined}'
    d = finding['details']
    assert d['split_artists'] == ['Camellia', 'Toby Fox']
    assert d['verified_via'] == 'spotify credits'
    assert d['track_count'] == 2 and d['file_count'] == 2
    assert d['library_v2']['track_ids'] == sorted(tracks)
    assert no_fetch == []          # the stored list decided, nobody was asked


def test_a_provider_listing_the_name_as_one_artist_keeps_it(db, no_fetch):
    # ghost "Nova" / "Vale" rows an old split left behind (#2131, #2136)
    tracks = _album_by(db, 'Nova & Vale', 'One')
    _identified(db, 'Nova', 'Vale')
    _credits(db, tracks[0], ['Nova & Vale'])
    _result, findings = _scan(db)
    assert findings == []


def test_a_slash_band_inside_a_semicolon_list_stays_whole(db, no_fetch):
    """#2150: "K/DA; League Of Legends; ..." was "K; DA; ..." with album artist "K"."""
    tracks = _album_by(db, KDA, 'Pop/Stars')
    _identified(db, 'K', 'DA')             # even identified fragments
    _credits(db, tracks[0], ['K/DA', 'League Of Legends', 'Mimi Webb', 'Sophie Powers'])
    _result, findings = _scan(db)
    assert findings[0]['details']['split_artists'] == [
        'K/DA', 'League Of Legends', 'Mimi Webb', 'Sophie Powers']
    assert findings[0]['details']['primary_artist'] == 'K/DA'


def test_the_albums_own_artist_inside_a_guest_credit_splits_it(db, no_fetch):
    _guest_on(db, '1788-L', '1788-L, Deathpact')
    _result, findings = _scan(db)
    d = findings[0]['details']
    assert d['split_artists'] == ['1788-L', 'Deathpact'] and d['primary_artist'] == '1788-L'
    assert d['verified_via'] == 'known'


def test_identified_library_artists_make_up_the_name(db, no_fetch):
    _album_by(db, 'Chase & Status, Becky Hill', 'Disconnect')
    _identified(db, 'Chase & Status', 'Becky Hill')
    _result, findings = _scan(db)
    assert findings[0]['details']['split_artists'] == ['Chase & Status', 'Becky Hill']


def test_a_name_nothing_explains_is_left_and_the_fetcher_was_asked(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One', 'Two', 'Three', 'Four')
    # plain rows prove nothing: that is what an old blind split leaves behind
    with db._get_connection() as conn:
        lib2_seed.artist(conn, 'Camellia')
        lib2_seed.artist(conn, 'Toby Fox')
        conn.commit()
    result, findings = _scan(db)
    assert findings == [] and result.skipped == 1
    assert no_fetch == sorted(tracks)[:3]      # a few of its tracks, not all


def test_what_the_fetcher_stores_decides(db, monkeypatch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')

    def fetch(database, track_id, services=None, **kw):
        _credits(db, track_id, ['Camellia', 'Toby Fox'])
        return True
    monkeypatch.setattr(pc, 'ensure_track_credits', fetch)
    _result, findings = _scan(db)
    assert findings[0]['details']['split_artists'] == ['Camellia', 'Toby Fox']
    assert findings[0]['details']['library_v2']['track_ids'] == tracks


def test_a_band_nothing_knows_and_keep_whole_names_are_never_flagged(db, no_fetch):
    _album_by(db, 'Earth, Wind & Fire', 'September')      # no data: lossless
    _album_by(db, 'Nova & Vale, Mat Zo', 'One')
    _identified(db, 'Nova', 'Vale', 'Mat Zo')
    _result, findings = _scan(db, _settings(keep_whole_names='Nova & Vale, Mat Zo'))
    assert findings == []
    assert len(no_fetch) == 1          # only the unknown band was worth asking about


def test_a_band_the_library_identified_is_never_flagged(db, no_fetch):
    _album_by(db, 'Lo & Leduc', 'Update 4.0', **{'spotify_id': 'sp-lo-leduc'})
    _identified(db, 'Lo', 'Leduc')         # even identified namesakes of its words
    _result, findings = _scan(db)
    assert findings == []


def test_a_kept_name_inside_a_credit_stays_one_part(db, no_fetch):
    _album_by(db, 'Nova & Vale, Mat Zo', 'One')
    _identified(db, 'Mat Zo')
    _result, findings = _scan(db, _settings(keep_whole_names='Nova & Vale'))
    assert findings[0]['details']['split_artists'] == ['Nova & Vale', 'Mat Zo']


def test_a_disabled_separator_is_never_split_even_on_evidence(db, no_fetch):
    tracks = _album_by(db, 'Camellia & Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    _result, findings = _scan(db, _settings(ampersand_splitter=False))
    assert findings == []


def test_an_artist_without_files_is_not_a_subject(db, no_fetch):
    with db._get_connection() as conn:
        lib2_seed.track(conn, 'Camellia, Toby Fox', 'Album', 'One', owned=False)
        conn.commit()
    result, findings = _scan(db)
    assert findings == [] and result.scanned == 0


def test_a_file_scope_narrows_the_run(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    _result, findings = _scan(db, scope={'file_paths': ['/somewhere/else.flac']})
    assert findings == []
    with db._get_connection() as conn:
        path = conn.execute("SELECT path FROM lib2_track_files").fetchone()[0]
    _result, findings = _scan(db, scope={'file_paths': [path]})
    assert len(findings) == 1


def test_a_capped_run_resumes_after_the_last_artist(db, no_fetch, monkeypatch):
    import core.repair_jobs.comma_artist_splitter as cas
    monkeypatch.setattr(cas, 'SCAN_ARTIST_LIMIT', 1)
    for name in ('A1, B1', 'A2, B2', 'A3, B3'):
        tracks = _album_by(db, name, 'One', album=name)
        _credits(db, tracks[0], name.split(', '))
    names = []
    cfg = _Cfg()
    for _ in range(3):
        _result, findings = _scan(db, cfg)
        names.extend(f['details']['combined_name'] for f in findings)
    assert names == ['A1, B1', 'A2, B2', 'A3, B3']


# ── the fix: heal the catalogue, then ask Re-tag / Reorganize ──────────────

def _finding(db, name):
    _result, findings = _scan(db)
    found = [f for f in findings if f['details']['combined_name'] == name]
    assert found, findings
    return found[0]


def test_the_fix_splits_the_artist_in_the_catalogue_only(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One', 'Two')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    worker = _worker(db)
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None, finding['details'])
    assert out['success'] is True and out['action'] == 'artists_split', out
    with db._get_connection() as conn:
        lead = conn.execute("SELECT ar.name FROM lib2_albums al JOIN lib2_artists ar "
                            "ON ar.id=al.primary_artist_id").fetchone()[0]
        credited = {r[0] for r in conn.execute(
            "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
            "WHERE ta.track_id=?", (tracks[1],))}
        gone = conn.execute("SELECT 1 FROM lib2_artists WHERE name='Camellia, Toby Fox'").fetchone()
    assert lead == 'Camellia' and credited == {'Camellia', 'Toby Fox'} and gone is None
    assert worker.asked == [(sorted(tracks), {'retag': True, 'reorganize': True,
                                              'reason': 'Split Artists "Camellia, Toby Fox"'})]
    assert out['message'] == ('"Camellia, Toby Fox" split into 2 artists; '
                              'Re-tag and Reorganize will check 2 file(s)')


def test_projection_follows_the_jobs_settings(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    worker = _worker(db, _settings(request_reorganize=False))
    worker._fix_comma_artist_split('artist', finding['entity_id'], None, finding['details'])
    assert worker.asked[0][1]['retag'] is True and worker.asked[0][1]['reorganize'] is False


def test_one_artist_keeps_the_name_and_remembers_it(db, no_fetch):
    tracks = _album_by(db, 'Nova & Vale, Mat Zo', 'One')
    _credits(db, tracks[0], ['Nova', 'Vale', 'Mat Zo'])
    finding = _finding(db, 'Nova & Vale, Mat Zo')
    cfg = _Cfg()
    worker = _worker(db, cfg)
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None,
                                         {**finding['details'], '_fix_action': 'keep_whole'})
    assert out['success'] is True and out['action'] == 'kept_whole'
    assert _artist_id(db, 'Nova & Vale, Mat Zo') is not None and worker.asked == []
    from core.matching.artist_credit import kept_whole_artist_names
    assert kept_whole_artist_names(cfg) == ['Nova & Vale, Mat Zo']


def test_an_edited_split_heals_with_the_users_names_and_remembers_the_joined_one(db, no_fetch):
    tracks = _album_by(db, 'Nova & Vale, Mat Zo', 'One')
    _credits(db, tracks[0], ['Nova', 'Vale', 'Mat Zo'])
    finding = _finding(db, 'Nova & Vale, Mat Zo')
    cfg = _Cfg()
    worker = _worker(db, cfg)
    out = worker._fix_comma_artist_split(
        'artist', finding['entity_id'], None,
        {**finding['details'], '_fix_action': 'split:Nova & Vale; Mat Zo'})
    assert out['success'] is True, out
    assert _artist_id(db, 'Nova & Vale') and _artist_id(db, 'Mat Zo')
    assert _artist_id(db, 'Nova') is None
    from core.matching.artist_credit import kept_whole_artist_names
    assert kept_whole_artist_names(cfg) == ['Nova & Vale']


def test_an_old_finding_that_breaks_a_known_name_is_refused(db, no_fetch):
    _album_by(db, KDA, 'Pop/Stars')
    _identified(db, 'K/DA')
    worker = _worker(db)
    out = worker._fix_comma_artist_split('artist', f'lib2:{_artist_id(db, KDA)}', None, {
        'combined_name': KDA, 'library_v2_native': True,
        'split_artists': ['K', 'DA', 'League Of Legends', 'Mimi Webb', 'Sophie Powers']})
    assert out['success'] is False and 'K/DA' in out['error']
    assert _artist_id(db, KDA) is not None


def test_a_lock_refuses_the_split(db, no_fetch, monkeypatch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    from core.library2 import locks
    monkeypatch.setattr(locks, 'blocks_entity',
                        lambda kind, entity_id, *a, **k: kind == 'track' and entity_id == tracks[0])
    worker = _worker(db)
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None, finding['details'])
    assert out['success'] is False and 'lock' in out['error']
    assert _artist_id(db, 'Camellia, Toby Fox') is not None and worker.asked == []


def test_a_hand_set_name_refuses_the_split(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    combined = _artist_id(db, 'Camellia, Toby Fox')
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, "
                     "value_json) VALUES('artist', ?, 'name', '\"Camellia, Toby Fox\"')",
                     (combined,))
        conn.commit()
    out = _worker(db)._fix_comma_artist_split('artist', finding['entity_id'], None,
                                              finding['details'])
    assert out['success'] is False and 'by hand' in out['error']


def test_an_older_tag_finding_with_the_catalogue_already_split_asks_retag_only(db, no_fetch):
    tracks = _album_by(db, 'Camellia', 'One')
    with db._get_connection() as conn:
        path = conn.execute("SELECT path FROM lib2_track_files").fetchone()[0]
    worker = _worker(db)
    out = worker._fix_comma_artist_split('track', f'lib2:{tracks[0]}', None, {
        'combined_name': 'Camellia, Toby Fox', 'library_v2_native': True,
        'split_artists': ['Camellia', 'Toby Fox'], 'all_files': [{'file_path': path}]})
    assert out['success'] is True and out['action'] == 'catalogue_already_split'
    assert worker.asked == [(tracks, {'retag': True, 'reorganize': False,
                                      'reason': 'Split Artists "Camellia, Toby Fox"'})]


def test_live_mode_applies_and_records_the_split(db, no_fetch, monkeypatch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    import core.repair_worker as rw
    worker = _worker(db, _settings(dry_run=False))
    monkeypatch.setattr(rw, '_ACTIVE_WORKER', worker)
    result, findings = _scan(db, _settings(dry_run=False))
    assert findings == [] and result.auto_fixed == 1
    assert _artist_id(db, 'Camellia, Toby Fox') is None and worker.asked
    with db._get_connection() as conn:
        row = conn.execute("SELECT status, user_action FROM repair_findings "
                           "WHERE job_id='comma_artist_splitter'").fetchone()
    assert tuple(row) == ('resolved', 'artists_split')


def test_the_finding_says_which_albums_move_to_the_lead(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')                 # its own album
    guest = _guest_on(db, 'Camellia', 'Camellia, Toby Fox', album='Other')  # a guest credit
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    with db._get_connection() as conn:
        own = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (tracks[0],)).fetchone()[0]
    assert finding['details']['primary_album_ids'] == [own]
    assert guest in finding['details']['library_v2']['track_ids']


# ── strict: the providers confirm a split, or the user does ─────────────────

def test_a_split_names_alone_propose_waits_for_the_user(db, no_fetch):
    # a ";" proposes "ODESZA" and "RY X", but no provider credits them on the
    # track: filed for the user to check, never applied on its own
    _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth')
    _result, findings = _scan(db)
    assert len(findings) == 1
    finding = findings[0]
    assert finding['severity'] == 'info'
    assert finding['details']['split_artists'] == ['ODESZA', 'RY X']
    assert finding['details']['needs_confirmation'] is True
    assert finding['details']['confirmed'] is False
    assert finding['details']['checked_sources'] == []


def test_a_split_every_part_of_which_a_provider_credits_is_confirmed(db, no_fetch):
    tracks = _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth')
    _credits(db, tracks[0], ['ODESZA', 'RY X'], source='musicbrainz')
    _result, findings = _scan(db)
    details = findings[0]['details']
    assert findings[0]['severity'] == 'warning'
    assert details['confirmed'] is True and details['needs_confirmation'] is False
    assert details['verified_via'] == 'musicbrainz credits'
    assert no_fetch == []                  # the stored list settled it


def test_a_part_no_provider_credits_leaves_the_split_unconfirmed(db, no_fetch):
    tracks = _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth')
    _credits(db, tracks[0], ['ODESZA'])    # Spotify credits ODESZA alone
    _result, findings = _scan(db)
    assert findings[0]['details']['needs_confirmation'] is True


def test_the_fetcher_looks_the_tracks_up_under_the_proposed_artists(db, monkeypatch):
    tracks = _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth')
    asked = []

    def ensure(database, track_id, services=None, **kw):
        asked.append(kw.get('artist_hints'))
        _credits(db, track_id, ['ODESZA', 'RY X'], source='deezer')
        return True
    monkeypatch.setattr(pc, 'ensure_track_credits', ensure)
    _result, findings = _scan(db)
    assert asked == [['ODESZA', 'RY X']]
    assert findings[0]['details']['confirmed'] is True and tracks


def test_live_mode_applies_only_what_the_providers_confirmed(db, no_fetch, monkeypatch):
    confirmed = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, confirmed[0], ['Camellia', 'Toby Fox'])
    _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth', album='A Moment Apart')
    import core.repair_worker as rw
    worker = _worker(db, _settings(dry_run=False))
    monkeypatch.setattr(rw, '_ACTIVE_WORKER', worker)
    result, findings = _scan(db, _settings(dry_run=False))
    assert result.auto_fixed == 1 and _artist_id(db, 'Camellia, Toby Fox') is None
    assert [f['details']['combined_name'] for f in findings] == ['ODESZA; RY X']
    assert _artist_id(db, 'ODESZA; RY X') is not None


def test_fix_all_leaves_an_unconfirmed_split_its_own_button_applies_it(db, no_fetch):
    _album_by(db, 'ODESZA; RY X', 'Corners Of The Earth')
    finding = _finding(db, 'ODESZA; RY X')
    worker = _worker(db)
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None, finding['details'])
    assert out['success'] is False and out['skipped_by_design'] is True
    assert _artist_id(db, 'ODESZA; RY X') is not None and worker.asked == []
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None,
                                         {**finding['details'], '_explicit_single_fix': True})
    assert out['success'] is True and out['action'] == 'artists_split'
    assert _artist_id(db, 'ODESZA; RY X') is None


# ── a request for named artists (another job handing its candidates over) ──

def test_a_request_checks_exactly_the_artists_asked_for_files_or_not(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    _album_by(db, 'Nova, Mat Zo', 'Two', album='Other')
    with db._get_connection() as conn:
        # a fileless collaboration ghost, credited nowhere
        ghost = conn.execute("INSERT INTO lib2_artists(name, sort_name) "
                             "VALUES('Nelly & Florida Georgia Line', 'x')").lastrowid
        conn.commit()
    camellia = _artist_id(db, 'Camellia, Toby Fox')
    _result, findings = _scan(db, scope={'artist_ids': [camellia, ghost]})
    names = sorted(f['details']['combined_name'] for f in findings)
    assert 'Nova, Mat Zo' not in names                  # not asked for
    assert 'Camellia, Toby Fox' in names
    # the fileless ghost was checked too: nothing proposes a split (both
    # parts unknown), so it stands, untouched
    assert _result.scanned == 2
    assert _artist_id(db, 'Nelly & Florida Georgia Line') == ghost


def test_2136_the_job_never_cuts_a_band_into_its_words(db, no_fetch):
    """#2131 / #2136: ghost "Above" and "Beyond" rows from an old split, the
    identified band, and a credit naming the band and a guest"""
    _album_by(db, 'Above & Beyond', 'Sun & Moon', **{'spotify_id': 'sp-ab'})
    _identified(db, 'Above', 'Beyond', 'Mat Zo')
    tracks = _album_by(db, 'Above & Beyond & Mat Zo', 'Thing Called Love', album='Collab')
    _credits(db, tracks[0], ['Above & Beyond', 'Mat Zo'])
    _result, findings = _scan(db)
    assert [f['details']['combined_name'] for f in findings] == ['Above & Beyond & Mat Zo']
    finding = findings[0]
    assert finding['details']['split_artists'] == ['Above & Beyond', 'Mat Zo']
    assert finding['details']['confirmed'] is True
    out = _worker(db)._fix_comma_artist_split('artist', finding['entity_id'], None,
                                              finding['details'])
    assert out['success'] is True
    with db._get_connection() as conn:
        credited = {r[0] for r in conn.execute(
            "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
            "WHERE ta.track_id=?", (tracks[0],))}
    assert credited == {'Above & Beyond', 'Mat Zo'}


def test_an_anchored_ghost_with_an_id_it_picked_up_by_name_is_still_a_list(db, no_fetch):
    """e7923c21e: "Øneheart, Malek" on Øneheart's album got a provider id from
    a name match; the album's own artist inside the name proposes the split
    whatever id the ghost carries, the provider list confirms it"""
    tracks = _album_by(db, 'Øneheart', 'Snow', **{'spotify_id': 'sp-one'})
    with db._get_connection() as conn:
        ghost = conn.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id) "
                             "VALUES('Øneheart, Malek', 'x', 'sp-wrong')").lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'featured', 1)", (tracks[0], ghost))
        conn.commit()
    _credits(db, tracks[0], ['Øneheart', 'Malek'])
    result, findings = _scan(db)
    # a ghost, confirmed: healed on its own, its own picked-up id no obstacle
    assert findings == [] and result.auto_fixed == 1
    assert _artist_id(db, 'Øneheart, Malek') is None and _artist_id(db, 'Malek')



# ── every separator the kernel reads ────────────────────────────────────────

def test_split_artists_splits_at_every_separator_the_kernel_reads():
    symbols = [',', ';', '/', '&']
    assert _splits_only_at('Drake feat. Wizkid', ['Drake', 'Wizkid'], symbols)
    assert _splits_only_at('A x B', ['A', 'B'], symbols)
    assert _splits_only_at('A vs. B', ['A', 'B'], symbols)
    assert _splits_only_at('A | B', ['A', 'B'], symbols)
    assert _splits_only_at('Camellia and Toby Fox', ['Camellia', 'Toby Fox'], symbols)
    # the switches still decide the separators that can be part of a name
    assert not _splits_only_at('Camellia, Toby Fox', ['Camellia', 'Toby Fox'], [';'])
    assert not _splits_only_at('Camellia and Toby Fox', ['Camellia', 'Toby Fox'], [','])
    assert _splits_only_at('Drake feat. Wizkid', ['Drake', 'Wizkid'], [])
    # "Lil Nas X" has no separator in it
    assert not _splits_only_at('Lil Nas X', ['Lil Nas', ''], symbols)


def test_a_feat_credit_the_import_kept_whole_is_split_when_confirmed(db, no_fetch):
    """the import keeps "Drake feat. Wizkid" as written and hands it over;
    Split Artists reads the feat. and the provider list confirms it"""
    tracks = _album_by(db, 'Drake', 'One Dance', **{'spotify_id': 'sp-drake'})
    with db._get_connection() as conn:
        ghost = conn.execute("INSERT INTO lib2_artists(name, sort_name) "
                             "VALUES('Drake feat. Wizkid', 'x')").lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'featured', 1)", (tracks[0], ghost))
        conn.commit()
    _credits(db, tracks[0], ['Drake', 'Wizkid'])
    result, findings = _scan(db, scope={'artist_ids': [ghost]})
    # a confirmed ghost (a credit only) heals on its own, dry run or not
    assert findings == [] and result.auto_fixed == 1
    assert _artist_id(db, 'Drake feat. Wizkid') is None
    with db._get_connection() as conn:
        credited = {r[0] for r in conn.execute(
            "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
            "WHERE ta.track_id=?", (tracks[0],))}
    assert credited == {'Drake', 'Wizkid'}


def test_a_run_with_files_and_artists_asked_for_checks_both(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    _album_by(db, 'Nova, Mat Zo', 'Two', album='Other')
    with db._get_connection() as conn:
        path = conn.execute("SELECT path FROM lib2_track_files WHERE track_id=?",
                            (tracks[0],)).fetchone()[0]
        ghost = conn.execute("INSERT INTO lib2_artists(name, sort_name) "
                             "VALUES('Drake feat. Wizkid', 'x')").lastrowid
        conn.commit()
    _result, findings = _scan(db, scope={'file_paths': [path], 'artist_ids': [ghost]})
    names = {f['details']['combined_name'] for f in findings}
    assert 'Camellia, Toby Fox' in names            # the file scope
    assert 'Nova, Mat Zo' not in names               # neither asked nor in scope



# ── ghosts heal on their own, everything else is a finding in dry run ───────

def _ghost_on(db, album_artist, ghost, title='Song'):
    tracks = _album_by(db, album_artist, title, album=f'{album_artist} LP')
    with db._get_connection() as conn:
        gid = conn.execute("INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)",
                           (ghost, ghost)).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'featured', 1)", (tracks[0], gid))
        conn.commit()
    return tracks[0], gid


def test_a_confirmed_ghost_heals_in_dry_run_and_leaves_a_record(db, no_fetch):
    track, ghost = _ghost_on(db, 'Camellia', 'Camellia, Toby Fox')
    _credits(db, track, ['Camellia', 'Toby Fox'])
    result, findings = _scan(db)
    assert findings == [] and result.auto_fixed == 1
    assert _artist_id(db, 'Camellia, Toby Fox') is None
    with db._get_connection() as conn:
        row = conn.execute("SELECT status FROM repair_findings "
                           "WHERE job_id='comma_artist_splitter'").fetchone()
    assert row[0] == 'resolved'         # Fixed on the Tools page says what happened


def test_an_unconfirmed_ghost_is_a_finding_to_check(db, no_fetch):
    _ghost_on(db, 'Camellia', 'Camellia, Toby Fox')
    result, findings = _scan(db)
    assert result.auto_fixed == 0
    assert [f['details']['needs_confirmation'] for f in findings] == [True]


def test_what_is_not_a_ghost_stays_a_finding_in_dry_run(db, no_fetch):
    # an album of its own
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    # a watched credit
    track, watched = _ghost_on(db, 'Nova', 'Nova, Mat Zo', title='Two')
    _credits(db, track, ['Nova', 'Mat Zo'])
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_artists SET monitored=1 WHERE id=?", (watched,))
        conn.commit()
    result, findings = _scan(db)
    assert result.auto_fixed == 0
    assert sorted(f['details']['combined_name'] for f in findings) == [
        'Camellia, Toby Fox', 'Nova, Mat Zo']


def test_heal_ghosts_off_files_ghosts_as_findings(db, no_fetch):
    track, _ghost = _ghost_on(db, 'Camellia', 'Camellia, Toby Fox')
    _credits(db, track, ['Camellia', 'Toby Fox'])
    result, findings = _scan(db, _settings(heal_ghosts=False))
    assert result.auto_fixed == 0 and len(findings) == 1



# ── every name of the removed built-in list, through the job ────────────────

from tests.matching.test_artist_credit_corpus import OLD_KNOWN_ACTS  # noqa: E402


@pytest.mark.parametrize("band", OLD_KNOWN_ACTS)
def test_split_artists_never_splits_a_name_the_old_list_held(db, no_fetch, band):
    from core.matching.artist_credit import tokens
    # its words are artists in the library (old phantom splits), the worst case
    words = [w for w in tokens(band)[0] if w.casefold() != band.casefold()]
    _identified(db, *words)
    # the band as a credit on someone's track, a provider listing it whole
    track, _ghost = _ghost_on(db, 'Various Artists', band)
    _credits(db, track, [band])
    result, findings = _scan(db)
    assert result.auto_fixed == 0 and findings == []
    assert _artist_id(db, band) is not None


def test_an_old_finding_on_an_alias_never_splits_the_lead(db, no_fetch):
    """Nezreka's review, 4: after a legacy-backed heal the combined row stays
    as an alias of the lead. A pending finding on it must not heal the lead"""
    tracks = _album_by(db, 'Alpha', 'One', **{'spotify_id': 'sp-alpha'})
    alpha = _artist_id(db, 'Alpha')
    with db._get_connection() as conn:
        alias = conn.execute("INSERT INTO lib2_artists(name, sort_name, canonical_artist_id) "
                             "VALUES('Alpha & Beta', 'x', ?)", (alpha,)).lastrowid
        conn.commit()
    details = {'combined_name': 'Alpha & Beta', 'split_artists': ['Alpha', 'Beta'],
               'primary_artist': 'Alpha', 'db_artist_id': alias, 'library_v2_native': True,
               'confirmed': True, 'needs_confirmation': False,
               'library_v2': {'artist_ids': [alias], 'track_ids': tracks}}
    out = _worker(db)._fix_comma_artist_split('artist', f'lib2:{alias}', None, details)
    assert out['success'] is True and out['action'] == 'catalogue_already_split'
    with db._get_connection() as conn:
        row = conn.execute("SELECT spotify_id FROM lib2_artists WHERE id=?", (alpha,)).fetchone()
    assert row is not None and row[0] == 'sp-alpha'
    assert _artist_id(db, 'Beta') is None


def test_a_dismissed_split_never_heals_on_its_own(db, no_fetch):
    """Nezreka's review, 6: dry run, the user dismissed it, the ghost stays"""
    track, ghost = _ghost_on(db, 'Camellia', 'Camellia, Toby Fox')
    _credits(db, track, ['Camellia', 'Toby Fox'])
    with db._get_connection() as conn:
        conn.execute("INSERT INTO repair_findings(job_id, finding_type, severity, status, "
                     "entity_type, entity_id, title, description, details_json) "
                     "VALUES('comma_artist_splitter', 'comma_artist_split', 'warning', "
                     "'dismissed', 'artist', ?, 't', 'd', '{}')", (f'lib2:{ghost}',))
        conn.commit()
    for settings in (_settings(), _settings(dry_run=False)):
        result, _findings = _scan(db, settings)
        assert result.auto_fixed == 0
    assert _artist_id(db, 'Camellia, Toby Fox') == ghost


def test_a_combined_artist_on_a_profile_watchlist_is_no_ghost(db, no_fetch):
    track, _ghost = _ghost_on(db, 'Camellia', 'Camellia, Toby Fox')
    _credits(db, track, ['Camellia', 'Toby Fox'])
    with db._get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS watchlist_artists(id INTEGER PRIMARY KEY, "
                     "artist_name TEXT NOT NULL, spotify_artist_id TEXT)")
        conn.execute("INSERT INTO watchlist_artists(artist_name) VALUES('Camellia, Toby Fox')")
        conn.commit()
    result, findings = _scan(db)
    assert result.auto_fixed == 0 and len(findings) == 1


# ── bulk-fix: fixable set derived from the handler map ───────────────────────

def test_bulk_fixable_set_matches_fix_handlers(db):
    """The old hardcoded tuple silently skipped genre_cleanup /
    replaygain_retag / comma_artist_split in Fix All. Derivation pins them in."""
    handlers = _worker(db)._fix_handlers()
    # duplicate_tracks belongs to a deliberately retired legacy job; native
    # catalogue construction prevents that duplicate-row class.
    for ft in ('genre_cleanup', 'replaygain_retag', 'comma_artist_split', 'dead_file'):
        assert ft in handlers


def test_a_run_asks_the_providers_for_a_bounded_number_of_artists(db, no_fetch, monkeypatch):
    """Nezreka's review: no burst of provider calls over a big backlog"""
    import core.repair_jobs.comma_artist_splitter as cas
    import core.repair_worker as rw
    monkeypatch.setattr(cas, 'FETCH_ARTIST_BUDGET', 1)
    asked = []
    monkeypatch.setattr(rw, 'request_artist_split', lambda ids, reason='': asked.extend(ids) or True)
    _album_by(db, 'Nova, Mat Zo', 'One', album='A')
    _album_by(db, 'Camellia, Toby Fox', 'Two', album='B')
    ids = sorted([_artist_id(db, 'Nova, Mat Zo'), _artist_id(db, 'Camellia, Toby Fox')])
    result, _findings = _scan(db, scope={'artist_ids': ids})
    assert len(no_fetch) == 1 and result.scanned == 1
    assert asked == [ids[1]]           # the rest is asked for again, for the next run


def test_fix_all_checks_provider_evidence_still_supports_a_pending_split(db, no_fetch):
    tracks = _album_by(db, 'Camellia, Toby Fox', 'One')
    _credits(db, tracks[0], ['Camellia', 'Toby Fox'])
    finding = _finding(db, 'Camellia, Toby Fox')
    assert finding['details']['confirmed']
    with db._get_connection() as conn:
        conn.execute('DELETE FROM lib2_provider_credits WHERE entity_id=?', (tracks[0],))
        conn.commit()

    worker = _worker(db)
    out = worker._fix_comma_artist_split('artist', finding['entity_id'], None, finding['details'])
    assert out['skipped_by_design'] is True
    assert _artist_id(db, 'Camellia, Toby Fox') is not None
    assert worker.asked == []
