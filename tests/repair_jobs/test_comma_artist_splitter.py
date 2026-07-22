"""Comma Artist Splitter (jadux) — scan verification gates + tag-splitting fix.

The job flags an artist like "Camellia, Toby Fox" ONLY when the full string is
not a real artist (API check + whitelist) AND every comma part resolves to a
known artist (own library first, API second). Fail-safe throughout: no API
reachable, or one unresolvable part → no finding.

The fix re-tags the files (display "A; B" + multi-value artists list, album
artist to primary where it was the combined string) with a stale-tag guard.

Also pins the bulk-fix root-cause fix: ``bulk_fix_findings`` derives its
fixable set from the fix-handler map instead of a second hardcoded tuple that
had silently fallen behind (genre_cleanup / replaygain_retag findings counted
in "Fix All N" but were skipped by the fix loop).
"""

from __future__ import annotations

import json
import os
import struct

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.comma_artist_splitter import (
    CommaArtistSplitterJob,
    normalize_artist_name,
    split_comma_parts,
)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_flac(path, tags=None):
    """Minimal but real FLAC with synthetic frames — survives the atomic
    save's frame byte-compare (same recipe as test_atomic_audio_save)."""
    from mutagen.flac import FLAC
    si = bytearray(34)
    si[0:2] = struct.pack(">H", 4096)
    si[2:4] = struct.pack(">H", 4096)
    si[10] = 0x0A
    si[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    path.write_bytes(b"fLaC" + block_header + bytes(si) + bytes(range(256)) * 8)
    audio = FLAC(str(path))
    for k, v in (tags or {}).items():
        audio[k] = v if isinstance(v, list) else [v]
    audio.save()


class _FakeArtistClient:
    """search_artists stub. `known` = artist names it 'knows'."""

    def __init__(self, known=()):
        self.known = list(known)
        self.calls = []

    def search_artists(self, query, limit=20):
        self.calls.append(query)
        q = normalize_artist_name(query)
        return [{'name': n} for n in self.known
                if q in normalize_artist_name(n) or normalize_artist_name(n) == q]


class _RaisingClient:
    def search_artists(self, query, limit=20):
        raise ConnectionError("api down")


def _patch_clients(monkeypatch, mapping):
    """Route get_client_for_source to fakes. Missing source → None client."""
    import core.metadata_service as ms
    monkeypatch.setattr(ms, 'get_client_for_source', lambda s: mapping.get(s))


def _db(tmp_path):
    d = MusicDatabase(str(tmp_path / "music.db"))
    with d._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('DUMMY', 'Camellia, Toby Fox', 'test')")
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR_C', 'Camellia', 'test')")
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('AR_T', 'Toby Fox', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES ('AL1', 'Deltarune', 'DUMMY', 'test')")
        conn.commit()
    return d


def _add_track(db, tid, artist_id, path, title='Flower Man'):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
            "VALUES (?, ?, ?, ?, 'AL1', 'test')", (tid, title, path, artist_id))
        conn.commit()


def _ctx(db, findings):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=None,
        create_finding=lambda **kw: findings.append(kw) or True,
    )


def _run(db, monkeypatch, clients, findings=None):
    findings = findings if findings is not None else []
    _patch_clients(monkeypatch, clients)
    result = CommaArtistSplitterJob().scan(_ctx(db, findings))
    return result, findings


# ── unit: name helpers ───────────────────────────────────────────────────────

def test_split_comma_parts():
    assert split_comma_parts('Camellia, Toby Fox') == ['Camellia', 'Toby Fox']
    assert split_comma_parts('A, B, C') == ['A', 'B', 'C']
    assert split_comma_parts(' A ,') == ['A']
    assert split_comma_parts('') == []


def test_normalize_artist_name():
    assert normalize_artist_name('  Toby   FOX ') == 'toby fox'


# ── scan: the verification gates ─────────────────────────────────────────────

def test_flags_split_when_parts_in_library_and_api_says_not_real(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _add_track(db, 'T1', 'DUMMY', '/x/a.flac')
    result, findings = _run(db, monkeypatch, {'deezer': _FakeArtistClient()})
    assert result.findings_created == 1
    f = findings[0]
    assert f['finding_type'] == 'comma_artist_split'
    assert f['entity_id'] == 'DUMMY'
    d = f['details']
    assert d['split_artists'] == ['Camellia', 'Toby Fox']
    assert d['new_display_artist'] == 'Camellia; Toby Fox'
    assert d['primary_artist'] == 'Camellia'
    assert d['artist_id'] == 'DUMMY'          # clickable-card standard
    assert d['track_count'] == 1
    assert d['checked_sources'] == ['deezer']
    assert all(p['in_library'] for p in d['parts_resolution'])


def test_whitelisted_comma_artist_never_flagged_and_no_api_spent(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('TY', 'Tyler, The Creator', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES ('AL1', 'Igor', 'TY', 'test')")
        conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                     "VALUES ('T1', 'Earfquake', '/x/e.flac', 'TY', 'AL1', 'test')")
        conn.commit()
    client = _FakeArtistClient()
    result, findings = _run(db, monkeypatch, {'deezer': client})
    assert result.findings_created == 0
    assert findings == []
    assert client.calls == []                 # whitelist short-circuits the API


def test_full_string_found_on_api_is_skipped(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _add_track(db, 'T1', 'DUMMY', '/x/a.flac')
    client = _FakeArtistClient(known=['Camellia, Toby Fox'])
    result, findings = _run(db, monkeypatch, {'deezer': client})
    assert result.findings_created == 0
    assert findings == []


def test_no_api_reachable_is_failsafe_skip(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _add_track(db, 'T1', 'DUMMY', '/x/a.flac')
    result, findings = _run(db, monkeypatch, {'deezer': _RaisingClient(),
                                              'itunes': None, 'spotify': None})
    assert result.findings_created == 0
    assert findings == []


def test_unresolvable_part_kills_the_finding(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('D2', 'Nobody Knows, This Guy', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES ('AL1', 'X', 'D2', 'test')")
        conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                     "VALUES ('T1', 'Y', '/x/y.flac', 'D2', 'AL1', 'test')")
        conn.commit()
    result, findings = _run(db, monkeypatch, {'deezer': _FakeArtistClient()})
    assert result.findings_created == 0
    assert findings == []


def test_parts_can_resolve_via_api_when_not_in_library(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES ('D3', 'juno, dltzk', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES ('AL1', 'All Nighter', 'D3', 'test')")
        conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                     "VALUES ('T1', 'back off!!!', '/x/b.flac', 'D3', 'AL1', 'test')")
        conn.commit()
    client = _FakeArtistClient(known=['juno', 'dltzk'])
    result, findings = _run(db, monkeypatch, {'deezer': client})
    assert result.findings_created == 1
    res = findings[0]['details']['parts_resolution']
    assert [p['verified_via'] for p in res] == ['deezer', 'deezer']
    assert not any(p['in_library'] for p in res)


def test_dedup_counts_when_create_finding_returns_false(tmp_path, monkeypatch):
    db = _db(tmp_path)
    _add_track(db, 'T1', 'DUMMY', '/x/a.flac')
    _patch_clients(monkeypatch, {'deezer': _FakeArtistClient()})
    ctx = JobContext(db=db, transfer_folder='/tmp', config_manager=None,
                     create_finding=lambda **kw: False)
    result = CommaArtistSplitterJob().scan(ctx)
    assert result.findings_created == 0
    assert result.findings_skipped_dedup == 1


def test_artist_without_files_not_scanned(tmp_path, monkeypatch):
    db = _db(tmp_path)  # DUMMY exists but owns no tracks with files
    result, findings = _run(db, monkeypatch, {'deezer': _FakeArtistClient()})
    assert result.scanned == 0
    assert findings == []


# ── fix: tag splitting on real files ─────────────────────────────────────────

def _worker(db, tmp_path):
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path)
    return w


def _details():
    return {
        'combined_name': 'Camellia, Toby Fox',
        'split_artists': ['Camellia', 'Toby Fox'],
        'new_display_artist': 'Camellia; Toby Fox',
        'primary_artist': 'Camellia',
    }


def test_fix_splits_flac_artist_and_albumartist(tmp_path):
    from mutagen.flac import FLAC
    db = _db(tmp_path)
    f = tmp_path / "a.flac"
    _make_flac(f, {'artist': 'Camellia, Toby Fox', 'albumartist': 'Camellia, Toby Fox'})
    _add_track(db, 'T1', 'DUMMY', str(f))

    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None, _details())
    assert result['success'] is True
    assert result['action'] == 'artists_split'

    audio = FLAC(str(f))
    assert audio['artist'] == ['Camellia; Toby Fox']
    assert list(audio['artists']) == ['Camellia', 'Toby Fox']
    assert audio['albumartist'] == ['Camellia']


def test_fix_leaves_unrelated_albumartist_alone(tmp_path):
    from mutagen.flac import FLAC
    db = _db(tmp_path)
    f = tmp_path / "a.flac"
    _make_flac(f, {'artist': 'Camellia, Toby Fox', 'albumartist': 'Various Artists'})
    _add_track(db, 'T1', 'DUMMY', str(f))

    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None, _details())
    assert result['success'] is True
    assert FLAC(str(f))['albumartist'] == ['Various Artists']


def test_fix_stale_tag_guard_skips_edited_file(tmp_path):
    from mutagen.flac import FLAC
    db = _db(tmp_path)
    f = tmp_path / "a.flac"
    _make_flac(f, {'artist': 'Camellia'})     # user already fixed it by hand
    _add_track(db, 'T1', 'DUMMY', str(f))

    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None, _details())
    assert result['success'] is False
    assert 'no longer carry' in result['error']
    assert FLAC(str(f))['artist'] == ['Camellia']   # untouched


def test_fix_already_multivalue_counts_as_stale(tmp_path):
    from mutagen.flac import FLAC
    db = _db(tmp_path)
    f = tmp_path / "a.flac"
    _make_flac(f, {'artist': ['Camellia', 'Toby Fox']})   # already split
    _add_track(db, 'T1', 'DUMMY', str(f))

    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None, _details())
    assert result['success'] is False
    assert FLAC(str(f))['artist'] == ['Camellia', 'Toby Fox']


def test_fix_no_tracks_resolves_as_already_gone(tmp_path):
    db = _db(tmp_path)
    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None, _details())
    assert result['success'] is True
    assert result['action'] == 'already_gone'


def test_fix_rejects_finding_without_parts(tmp_path):
    db = _db(tmp_path)
    result = _worker(db, tmp_path)._fix_comma_artist_split('artist', 'DUMMY', None,
                                                           {'combined_name': 'X'})
    assert result['success'] is False


# ── bulk-fix: fixable set derived from the handler map ───────────────────────

def test_bulk_fixable_set_matches_fix_handlers(tmp_path):
    """The old hardcoded tuple silently skipped genre_cleanup /
    replaygain_retag / comma_artist_split in Fix All. Derivation pins them in."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    handlers = _worker(db, tmp_path)._fix_handlers()
    for ft in ('genre_cleanup', 'replaygain_retag', 'comma_artist_split',
               'dead_file', 'duplicate_tracks'):
        assert ft in handlers


def test_bulk_fix_now_fixes_genre_cleanup_findings(tmp_path):
    """End-to-end regression: a pending genre_cleanup finding is actually
    fixed by bulk-fix (it used to be silently filtered out → 'Fixed 0')."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, genres, server_source) VALUES "
                     "('AR1', 'Dirty', ?, 'test')", (json.dumps(['Rock', 'junk']),))
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
            "entity_type, entity_id, title, details_json) VALUES "
            "('genre_cleanup', 'genre_cleanup', 'info', 'pending', 'artist', 'AR1', "
            "'Off-whitelist genres: Dirty', ?)",
            (json.dumps({'kept_genres': ['Rock'], 'removed_genres': ['junk']}),))
        conn.commit()

    result = _worker(db, tmp_path).bulk_fix_findings(job_id='genre_cleanup')
    assert result.get('fixed') == 1

    with db._get_connection() as conn:
        genres = conn.execute("SELECT genres FROM artists WHERE id = 'AR1'").fetchone()[0]
        status = conn.execute("SELECT status FROM repair_findings").fetchone()[0]
    assert json.loads(genres) == ['Rock']
    assert status == 'resolved'


def test_bulk_fix_comma_artist_split_end_to_end(tmp_path):
    from mutagen.flac import FLAC
    db = _db(tmp_path)
    f = tmp_path / "a.flac"
    _make_flac(f, {'artist': 'Camellia, Toby Fox'})
    _add_track(db, 'T1', 'DUMMY', str(f))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
            "entity_type, entity_id, title, details_json) VALUES "
            "('comma_artist_splitter', 'comma_artist_split', 'warning', 'pending', "
            "'artist', 'DUMMY', 'Combined artist: Camellia, Toby Fox', ?)",
            (json.dumps(_details()),))
        conn.commit()

    result = _worker(db, tmp_path).bulk_fix_findings(job_id='comma_artist_splitter')
    assert result.get('fixed') == 1
    assert FLAC(str(f))['artist'] == ['Camellia; Toby Fox']


# ── UI contract pins (labels + detail renderer present) ──────────────────────

def test_enrichment_js_carries_the_ui_contract():
    js = open(os.path.join(os.path.dirname(__file__), '..', '..',
                           'webui', 'static', 'enrichment.js'), encoding='utf-8').read()
    assert "comma_artist_split: 'Comma Artist'" in js       # type badge
    assert "comma_artist_split: 'Split Artists'" in js      # fix button
    assert "artists_split: 'Artists Split'" in js           # resolved badge
    assert "case 'comma_artist_split':" in js               # detail renderer
