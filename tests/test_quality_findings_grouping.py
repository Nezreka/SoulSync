"""Sorting an upgrade backlog by how bad it actually is, and folding it to albums.

Two complaints from the same report (Lil-Uzi-Chimp, Aug 26 2026):

  "The sorting by severity feature currently doesn't work right ... I would
   expect to see the lowest bit rates first but I see 320 then 192 then 256 in
   random order."

  "An 'album' or 'artist' view would also be nice if I would like to fix an
   album."

The sort was not broken. The quality scanner only ever emits 'warning' (broken
audio) or 'info' (below profile), so every upgradeable track tied at 'info' and
the ORDER BY fell through to created_at - scan order, wearing severity's name.
Severity was never told how bad a file is.
"""

from __future__ import annotations

import json

import pytest

from core.repair_worker import RepairWorker


@pytest.fixture()
def worker(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    conn = db._get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS repair_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, finding_type TEXT,
        severity TEXT, status TEXT, entity_type TEXT, entity_id TEXT,
        file_path TEXT, title TEXT, description TEXT, details_json TEXT,
        created_at TEXT, updated_at TEXT)""")
    conn.commit()
    conn.close()
    return w


def _add(worker, *, artist, album, fmt, bitrate, severity='info',
         quality=None, created='2026-01-01', sample_rate=None, bit_depth=None):
    conn = worker.db._get_connection()
    details = {
        'current_format': fmt, 'current_bitrate': bitrate,
        'current_sample_rate': sample_rate, 'current_bit_depth': bit_depth,
        'current_quality': quality or f'{fmt.upper()} {bitrate}kbps',
        'expected_artist': artist, 'album_title': album,
        'album_thumb_url': f'/art/{album}.jpg',
        'artist_thumb_url': f'/art/{artist}.jpg',
        'artist_id': 'a-' + artist,
    }
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status,"
        " file_path, title, details_json, created_at) VALUES"
        " ('quality_upgrade','quality_upgrade',?,'pending',?,?,?,?)",
        (severity, f'/m/{artist}/{album}/{bitrate}.{fmt}',
         f'{artist} {bitrate}', json.dumps(details), created))
    conn.commit()
    conn.close()


def _labels(worker, sort):
    got = worker.get_findings(status='pending', sort=sort, limit=50)
    return [json.loads(i['details_json'] if isinstance(i.get('details_json'), str)
                       else '{}').get('current_quality')
            if not isinstance(i.get('details'), dict)
            else i['details'].get('current_quality')
            for i in got['items']]


# ── the actual complaint ─────────────────────────────────────────────────────
def test_worst_bitrate_comes_first_instead_of_scan_order(worker):
    """The exact sequence from the report: 320, 192, 256 inserted in that order."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=320, created='2026-01-01')
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=192, created='2026-01-02')
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=256, created='2026-01-03')

    assert _labels(worker, 'quality') == ['MP3 192kbps', 'MP3 256kbps', 'MP3 320kbps']


def test_severity_sort_no_longer_means_scan_order(worker):
    """Every below-profile finding is 'info', so severity used to decide nothing.
    It still leads (a broken file outranks a lossy one) but the tie now breaks
    on how bad the audio is."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=320, created='2026-01-01')
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=128, created='2026-01-02')
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=256, created='2026-01-03')

    assert _labels(worker, 'severity') == ['MP3 128kbps', 'MP3 256kbps', 'MP3 320kbps']


def test_a_broken_file_still_outranks_a_merely_lossy_one(worker):
    """Severity leads the sort on purpose: 'this file is corrupt' is a different
    kind of problem from 'this file is 192kbps', and must not be buried under a
    thousand of them however bad their bitrate."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=64, severity='info')
    _add(worker, artist='A', album='X', fmt='flac', bitrate=1000,
         severity='warning', quality='FLAC (broken)')

    assert _labels(worker, 'severity')[0] == 'FLAC (broken)'


def test_bitrate_never_lifts_one_format_over_another(worker):
    """Cross-format priority belongs to the ranked profile, not to a bitrate
    number - the same rule tier_score() follows. A 320kbps mp3 must not sort as
    better audio than a lossless file."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=320, quality='MP3 320kbps')
    _add(worker, artist='A', album='X', fmt='flac', bitrate=900,
         sample_rate=44100, bit_depth=16, quality='FLAC 16-bit')

    assert _labels(worker, 'quality') == ['MP3 320kbps', 'FLAC 16-bit']


# ── the album / artist view ──────────────────────────────────────────────────
def test_albums_lead_with_the_one_worth_fixing_most(worker):
    _add(worker, artist='A', album='Good', fmt='mp3', bitrate=320)
    _add(worker, artist='A', album='Bad', fmt='mp3', bitrate=128)
    _add(worker, artist='A', album='Bad', fmt='mp3', bitrate=128)

    groups = worker.get_finding_albums(group_by='album')
    assert [g['album'] for g in groups] == ['Bad', 'Good']
    assert groups[0]['count'] == 2
    assert groups[0]['worst_quality'] == 'MP3 128kbps'


def test_a_group_carries_the_artwork_so_the_grid_needs_no_lookup(worker):
    _add(worker, artist='Aphex Twin', album='SAW', fmt='mp3', bitrate=192)
    g = worker.get_finding_albums(group_by='album')[0]
    assert g['album_thumb_url'] == '/art/SAW.jpg'
    assert g['artist_thumb_url'] == '/art/Aphex Twin.jpg'
    assert g['artist_id'] == 'a-Aphex Twin'
    assert g['artist'] == 'Aphex Twin'


def test_the_artist_view_folds_every_album_together(worker):
    _add(worker, artist='A', album='One', fmt='mp3', bitrate=320)
    _add(worker, artist='A', album='Two', fmt='mp3', bitrate=128)
    _add(worker, artist='B', album='Three', fmt='mp3', bitrate=256)

    groups = worker.get_finding_albums(group_by='artist')
    assert [(g['artist'], g['count']) for g in groups] == [('A', 2), ('B', 1)]
    # worst member still names the group
    assert groups[0]['worst_quality'] == 'MP3 128kbps'


def test_a_finding_with_no_album_is_dropped_not_piled_into_unknown(worker):
    """An 'Unknown' bucket would be the biggest card on the page and mean
    nothing. A row that cannot be grouped simply is not in the grouped view."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=192)
    conn = worker.db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status,"
        " title, details_json, created_at) VALUES"
        " ('quality_upgrade','quality_upgrade','info','pending','orphan','{}','2026-01-01')")
    conn.commit()
    conn.close()

    groups = worker.get_finding_albums(group_by='album')
    assert len(groups) == 1
    assert groups[0]['album'] == 'X'


def test_the_artist_view_also_drops_a_row_with_no_artist(worker):
    """The album view is protected by its own album guard, so a broken artist
    guard hides behind it. The artist view has only the artist guard - this is
    the test that actually holds it up."""
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=192)
    conn = worker.db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status,"
        " title, details_json, created_at) VALUES"
        " ('quality_upgrade','quality_upgrade','info','pending','orphan','{}','2026-01-01')")
    conn.commit()
    conn.close()

    groups = worker.get_finding_albums(group_by='artist')
    assert [g['artist'] for g in groups] == ['A']


def test_grouping_respects_the_status_filter(worker):
    _add(worker, artist='A', album='X', fmt='mp3', bitrate=192)
    conn = worker.db._get_connection()
    conn.execute("UPDATE repair_findings SET status='dismissed'")
    conn.commit()
    conn.close()

    assert worker.get_finding_albums(status='pending') == []
    assert len(worker.get_finding_albums(status='dismissed')) == 1

def test_grouping_reads_whatever_key_the_job_happened_to_use(worker):
    """Fourteen job types record an album and artist, and they do not agree on
    the spelling: 'artist' vs 'artist_name' vs 'expected_artist'. Reading only
    the quality scanner's pair would have made this a quality-only view by
    accident."""
    import json as _json
    conn = worker.db._get_connection()
    for details in (
        {'artist_name': 'Legacy', 'album_name': 'Old Keys',
         'current_format': 'mp3', 'current_bitrate': 192, 'current_quality': 'MP3 192kbps'},
        {'artist': 'Common', 'album': 'Usual Keys',
         'current_format': 'mp3', 'current_bitrate': 128, 'current_quality': 'MP3 128kbps'},
    ):
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status,"
            " title, details_json, created_at) VALUES"
            " ('dupes','duplicate_tracks','info','pending','x',?, '2026-01-01')",
            (_json.dumps(details),))
    conn.commit()
    conn.close()

    groups = worker.get_finding_albums(group_by='album')
    assert [(g['artist'], g['album']) for g in groups] == [
        ('Common', 'Usual Keys'), ('Legacy', 'Old Keys')]

# ── the score IS tier_score, not a lookalike ─────────────────────────────────
_PARITY_CASES = [
    ('mp3', 128, None, None), ('mp3', 192, None, None), ('mp3', 256, None, None),
    ('mp3', 320, None, None), ('mp3', None, None, None),
    ('aac', 128, None, None), ('aac', 256, None, None), ('opus', 128, None, None),
    ('ogg', 320, None, None), ('wma', 128, None, None), ('weirdfmt', 64, None, None),
    ('flac', 1411, 44100, 16), ('flac', 2304, 96000, 24), ('flac', 9216, 192000, 24),
    ('flac', None, None, None), ('alac', 1411, 44100, 16),
    ('wav', 1411, 44100, 16), ('wav', 9216, 192000, 24), ('dsf', 5644, 2822400, 1),
]


def test_the_sql_score_is_tier_score_transcribed_not_reinvented(worker):
    """Every score, not just the order.

    The first version of this was a flat "format base + bitrate/1000" that only
    looked like tier_score. tier_score has TWO branches - lossless scores on
    sample rate and bit depth and ignores bitrate entirely, lossy scores on
    bitrate capped at 320 - and the flat version matched neither, and sent DSD
    to the top of the list. ALAC belongs in the lossless branch: scoring it on
    bitrate gave a CD-spec ALAC the full +10 and put it ABOVE a CD-spec FLAC,
    which is the same audio in a worse-supported container. Two definitions of
    "better audio" in one app is how the ranker and the scanner start
    disagreeing.
    """
    import json as _json
    import sqlite3
    from core.quality.model import AudioQuality
    from core.repair_worker import RepairWorker

    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE t (details_json TEXT)")
    for fmt, br, sr, bd in _PARITY_CASES:
        conn.execute("INSERT INTO t VALUES (?)", (_json.dumps({
            'current_format': fmt, 'current_bitrate': br,
            'current_sample_rate': sr, 'current_bit_depth': bd}),))

    got = [round(r[0], 6) for r in conn.execute(
        f"SELECT {RepairWorker._QUALITY_SCORE_SQL} FROM t ORDER BY rowid")]
    want = [round(AudioQuality(format=f, bitrate=b, sample_rate=s, bit_depth=d).tier_score(), 6)
            for f, b, s, d in _PARITY_CASES]

    assert got == want, "the SQL score drifted from AudioQuality.tier_score()"


def test_lossless_ignores_bitrate_the_way_tier_score_does(worker):
    """A FLAC's bitrate is a consequence of its sample rate and depth, so
    tier_score reads the spec directly. Scoring lossless on bitrate would rank
    a 24/48 file above a 16/192 one on file size alone."""
    _add(worker, artist='A', album='X', fmt='flac', bitrate=9999,
         sample_rate=44100, bit_depth=16, quality='FLAC 16/44')
    _add(worker, artist='A', album='X', fmt='flac', bitrate=1,
         sample_rate=192000, bit_depth=24, quality='FLAC 24/192')

    # worst first: the 16/44 file, despite carrying the far larger bitrate
    assert _labels(worker, 'quality') == ['FLAC 16/44', 'FLAC 24/192']


def test_get_finding_albums_returns_finding_types_and_error_count(worker):
    """Album groups should aggregate distinct finding types and count errors."""
    _add(worker, artist='Radiohead', album='In Rainbows', fmt='mp3', bitrate=128)
    conn = worker.db._get_connection()
    # Add a corrupt_audio finding to the same album
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, title, details_json, created_at) "
        "VALUES ('audio_corruption', 'corrupt_audio', 'error', 'pending', 'Corrupt Song', ?, '2026-01-02')",
        (json.dumps({'expected_artist': 'Radiohead', 'album_title': 'In Rainbows', 'current_format': 'flac'}),)
    )
    conn.commit()
    conn.close()

    groups = worker.get_finding_albums(group_by='album')
    assert len(groups) == 1
    g = groups[0]
    assert g['artist'] == 'Radiohead'
    assert g['album'] == 'In Rainbows'
    assert g['count'] == 2
    assert set(g['finding_types']) == {'quality_upgrade', 'corrupt_audio'}
    assert g['error_count'] == 0


# ── the album tray acts on its own album only (review 5 W3-TU-1) ─────────────
def _short_title_library(worker):
    """'FM' by one artist, older than a page of other albums whose paths and
    details hold 'FM' too, the way the live re-tag backlog does."""
    _add(worker, artist='Makeup And Vanity Set', album='FM', fmt='mp3',
         bitrate=128, created='2026-01-01')
    for n in range(120):
        _add(worker, artist=f'Other {n}', album=f'FM Radio {n}', fmt='mp3',
             bitrate=192, created=f'2026-02-{1 + n % 28:02d}')
    _add(worker, artist='Someone Else', album='FM', fmt='mp3', bitrate=128,
         created='2026-03-01')


def _ids(worker, **kw):
    return [i['id'] for i in worker.get_findings(status='pending', limit=500, **kw)['items']]


def test_album_rows_come_by_the_group_key_not_a_title_search(worker):
    _short_title_library(worker)
    got = worker.get_findings(status='pending', limit=100, group_by='album',
                              group_artist='Makeup And Vanity Set', group_album='FM')
    assert got['total'] == 1
    assert [i['details']['expected_artist'] for i in got['items']] == ['Makeup And Vanity Set']
    # an album that can't be named exactly matches nothing, never everything
    assert worker.get_findings(status='pending', group_by='album',
                               group_artist='Makeup And Vanity Set', group_album='')['total'] == 0


def test_dismiss_all_refuses_ids_from_other_albums(worker):
    _short_title_library(worker)
    own = _ids(worker, group_by='album', group_artist='Makeup And Vanity Set', group_album='FM')
    everything = _ids(worker)
    result = worker.album_group_action('dismiss', 'album', 'Makeup And Vanity Set', 'FM',
                                       finding_ids=everything)
    assert result['updated'] == 1
    assert result['refused'] == len(everything) - 1
    left = set(_ids(worker))
    assert left == set(everything) - set(own)


def test_resolve_album_fixes_only_the_groups_ids(worker, monkeypatch):
    _short_title_library(worker)
    own = _ids(worker, group_by='album', group_artist='Makeup And Vanity Set', group_album='FM')
    seen = []

    def fake_bulk_fix(finding_ids=None, **_kw):
        seen.append(list(finding_ids or []))
        return {'fixed': len(finding_ids or []), 'failed': 0, 'skipped': 0, 'errors': []}

    monkeypatch.setattr(worker, 'bulk_fix_findings', fake_bulk_fix)
    result = worker.album_group_action('fix', 'album', 'Makeup And Vanity Set', 'FM',
                                       finding_ids=_ids(worker))
    assert seen == [own]
    assert result['fixed'] == 1
    # nothing of the group's in the request: no fix runs at all, not an
    # unscoped one
    seen.clear()
    other = [i for i in _ids(worker) if i not in own]
    result = worker.album_group_action('fix', 'album', 'Makeup And Vanity Set', 'FM',
                                       finding_ids=other[:5])
    assert seen == [] and result['fixed'] == 0 and result['refused'] == 5


def test_album_action_route_scopes_to_the_group(worker):
    from flask import Flask

    import api.repair as repair_api

    _short_title_library(worker)
    everything = _ids(worker)
    repair_api.configure(worker_getter=lambda: worker,
                         image_url_fixer=lambda url: url, metadata_cache=None)
    app = Flask(__name__)
    app.register_blueprint(repair_api.create_blueprint())
    client = app.test_client()

    listed = client.get('/api/repair/findings', query_string={
        'status': 'pending', 'limit': 100, 'group_by': 'album',
        'group_artist': 'Makeup And Vanity Set', 'group_album': 'FM'}).get_json()
    assert listed['total'] == 1

    res = client.post('/api/repair/findings/album-action', json={
        'action': 'dismiss', 'group_by': 'album', 'artist': 'Makeup And Vanity Set',
        'album': 'FM', 'ids': everything})
    body = res.get_json()
    assert res.status_code == 200 and body['updated'] == 1
    assert len(_ids(worker)) == len(everything) - 1


# ── a drilled-in card in the grouped list (review 5 W3-TT-2) ─────────────────
def test_clear_with_a_drilled_in_card_deletes_only_that_album(worker):
    """Delete matching with an album card open deleted every finding whose
    text held the album's name, of every type"""
    from flask import Flask

    import api.repair as repair_api

    _short_title_library(worker)
    everything = _ids(worker)
    repair_api.configure(worker_getter=lambda: worker,
                         image_url_fixer=lambda url: url, metadata_cache=None)
    app = Flask(__name__)
    app.register_blueprint(repair_api.create_blueprint())
    res = app.test_client().post('/api/repair/findings/clear', json={
        'status': 'pending', 'finding_type': 'quality_upgrade', 'group_by': 'album',
        'group_artist': 'Makeup And Vanity Set', 'group_album': 'FM'}).get_json()
    assert res['deleted'] == 1
    assert len(_ids(worker)) == len(everything) - 1
    assert _ids(worker, group_by='album', group_artist='Someone Else', group_album='FM')


def test_selected_rows_outside_the_drilled_card_are_refused(worker):
    _short_title_library(worker)
    own = _ids(worker, group_by='album', group_artist='Makeup And Vanity Set', group_album='FM')
    assert worker.ids_in_view(_ids(worker), status='pending', finding_type='quality_upgrade',
                              group_by='album', group_artist='Makeup And Vanity Set',
                              group_album='FM') == own
