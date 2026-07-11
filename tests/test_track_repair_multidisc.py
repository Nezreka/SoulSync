"""Track Number Repair vs multi-disc albums (#1009).

QT3496's box sets live flat in one folder with $disc$track filenames
('0213 - X' = disc 2, track 13). The repair job:
  * declared every multi-disc album anomalous — bare track numbers repeat once
    per disc, so 'track 1 × 5 discs' looked like the all-tracks-say-01 bug;
  * "repaired" already-correct files — the tag check compared the per-disc
    total against the WHOLE-album count;
  * mangled the 4-digit prefix — replacing the first 1-3 digits of '0213'
    with the 2-digit track wrote '133 - X' (the stray tail digit the reporter
    read as "a digit from the total track count").

These pin the disc-aware behavior: (disc, track)-keyed anomaly detection,
per-disc totals, DDTT-preserving renames — and that the single-disc all-01
rescue the job exists for still works.
"""

from __future__ import annotations

import struct
from pathlib import Path

from mutagen.flac import FLAC

from core.repair_jobs.base import JobContext
from core.repair_jobs.track_number_repair import (
    TrackNumberRepairJob,
    _plan_track_repair,
    _planned_prefix,
)


def _make_flac(path: Path, tags: dict | None = None) -> None:
    """Create a real, minimal FLAC (so we exercise the true mutagen path)."""
    fLaC = b'fLaC'
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])  # last block, STREAMINFO, len 34
    path.write_bytes(fLaC + block_header + bytes(streaminfo))
    audio = FLAC(str(path))
    if tags:
        for k, v in tags.items():
            audio[k] = [v]
    audio.save()


def _box_set_tracklist():
    """The reporter's shape: 3 discs, 40 tracks total (13 + 14 + 13).
    Disc 2 track 13 is 'Thirteen Lights'."""
    tracks = []
    for disc, count in ((1, 13), (2, 14), (3, 13)):
        for n in range(1, count + 1):
            name = f"Disc{disc} Song {n:02d}"
            if disc == 2 and n == 13:
                name = "Thirteen Lights"
            tracks.append({'name': name, 'track_number': n, 'disc_number': disc})
    assert len(tracks) == 40
    return tracks


def _ctx(findings=None, transfer="/tmp"):
    return JobContext(
        db=None, transfer_folder=str(transfer), config_manager=None,
        create_finding=(lambda **kw: findings.append(kw) or True) if findings is not None else None,
    )


# ── the corrected prefix, convention-preserving ──────────────────────────────

def test_planned_prefix_preserves_the_files_own_convention():
    # multi-disc DDTT stays DDTT, rebuilt from the matched disc+track
    assert _planned_prefix("0113", 13, 2, multi_disc=True) == "0213"
    assert _planned_prefix("0213", 13, 2, multi_disc=True) == "0213"
    # plain track prefixes stay track-only (2-digit), on any album
    assert _planned_prefix("01", 3, 1, multi_disc=False) == "03"
    assert _planned_prefix("1", 11, 3, multi_disc=True) == "11"
    assert _planned_prefix("001", 7, 1, multi_disc=False) == "07"
    # a 4-digit prefix on a SINGLE-disc album is a year ('1999 - …'), not a
    # track number — and 5+ digits is nothing we understand. Hands off.
    assert _planned_prefix("1999", 5, 1, multi_disc=False) is None
    assert _planned_prefix("18000", 5, 1, multi_disc=True) is None
    assert _planned_prefix("", 5, 1, multi_disc=True) is None


# ── correct multi-disc files are left alone ──────────────────────────────────

def test_correct_ddtt_file_is_not_flagged(tmp_path):
    """Disc 2 track 13 named '0213 - …' with a per-disc total tag: nothing to fix."""
    f = tmp_path / "0213 - Thirteen Lights.flac"
    _make_flac(f, {'title': 'Thirteen Lights', 'tracknumber': '13/14', 'discnumber': '2/3'})
    plan = _plan_track_repair(str(f), f.name, _box_set_tracklist(), 0.8)
    assert plan is None


def test_legacy_whole_album_total_is_not_flagged(tmp_path):
    """Files the OLD job repaired carry '13/40' (whole-album total). Accepted —
    otherwise the fix would re-flag every previously-repaired library."""
    f = tmp_path / "0213 - Thirteen Lights.flac"
    _make_flac(f, {'title': 'Thirteen Lights', 'tracknumber': '13/40', 'discnumber': '2/3'})
    assert _plan_track_repair(str(f), f.name, _box_set_tracklist(), 0.8) is None


# ── the mangler is dead: repairs rebuild DDTT, never splice digits ───────────

def test_wrong_ddtt_prefix_is_rebuilt_not_spliced(tmp_path):
    """A disc-2 file wrongly named '0113 - …' becomes '0213 - …' — the old code
    produced '133 - …' ('011'→'13' + the stray tail '3')."""
    f = tmp_path / "0113 - Thirteen Lights.flac"
    _make_flac(f, {'title': 'Thirteen Lights', 'tracknumber': '13/40', 'discnumber': '2/3'})
    plan = _plan_track_repair(str(f), f.name, _box_set_tracklist(), 0.8)
    assert plan is not None
    assert plan['new_basename'] == "0213 - Thirteen Lights"
    assert plan['tag_ok'] is True          # the tag itself was fine
    assert plan['correct_disc'] == 2 and plan['correct_num'] == 13


def test_disc_tag_breaks_title_ties_across_discs(tmp_path):
    """Same title on two discs: the file's disc decides which API track wins."""
    tracks = [
        {'name': 'Intro', 'track_number': 1, 'disc_number': 1},
        {'name': 'Alpha Beta', 'track_number': 2, 'disc_number': 1},
        {'name': 'Intro', 'track_number': 1, 'disc_number': 2},
        {'name': 'Gamma Delta', 'track_number': 2, 'disc_number': 2},
    ]
    f = tmp_path / "0299 - Intro.flac"       # disc 2, absurd track number
    _make_flac(f, {'title': 'Intro', 'tracknumber': '99', 'discnumber': '2'})
    plan = _plan_track_repair(str(f), f.name, tracks, 0.8)
    assert plan is not None
    assert plan['correct_disc'] == 2 and plan['correct_num'] == 1
    assert plan['new_basename'] == "0201 - Intro"


# ── anomaly detection is (disc, track)-keyed ─────────────────────────────────

def test_flat_box_set_is_not_anomalous(tmp_path):
    """Three discs' track 1 in one folder is normal, not the all-01 bug — the
    job must not even try to resolve a tracklist for it."""
    for disc in (1, 2, 3):
        _make_flac(tmp_path / f"0{disc}01 - Disc{disc} Song 01.flac",
                   {'title': f'Disc{disc} Song 01', 'tracknumber': '1/13',
                    'discnumber': f'{disc}/3'})
    job = TrackNumberRepairJob()
    job._resolve_album_tracklist = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("tracklist resolution must not run for a healthy box set"))
    findings = []
    result = job._repair_album(
        str(tmp_path), sorted(p.name for p in tmp_path.iterdir()),
        anomaly_threshold=3, context=_ctx(findings, tmp_path),
        scan_state={'album_tracks_cache': {}, 'title_similarity': 0.8, 'dry_run': True})
    assert findings == []
    assert result.findings_created == 0


def test_single_disc_all_01_bug_is_still_detected(tmp_path):
    """The rescue this job exists for: every file says track 1 (no disc tags).
    Still anomalous, still repaired."""
    tracks = [{'name': n, 'track_number': i + 1, 'disc_number': 1}
              for i, n in enumerate(['Alpha Song', 'Beta Song', 'Gamma Song'])]
    for name in ('Alpha Song', 'Beta Song', 'Gamma Song'):
        _make_flac(tmp_path / f"01 - {name}.flac",
                   {'title': name, 'tracknumber': '1'})
    job = TrackNumberRepairJob()
    job._resolve_album_tracklist = lambda *a, **k: tracks
    findings = []
    job._repair_album(
        str(tmp_path), sorted(p.name for p in tmp_path.iterdir()),
        anomaly_threshold=3, context=_ctx(findings, tmp_path),
        scan_state={'album_tracks_cache': {}, 'title_similarity': 0.8, 'dry_run': True})
    # Alpha (track 1) is correct; Beta -> 2 and Gamma -> 3 get findings
    assert len(findings) == 2
    fixed = {f['details']['correct_track_num'] for f in findings}
    assert fixed == {2, 3}
    # and the proposed renames are plain 2-digit track prefixes
    changes = "\n".join("\n".join(f['details']['changes']) for f in findings)
    assert "02 - Beta Song.flac" in changes and "03 - Gamma Song.flac" in changes


# ── the approval path applies the promised plan (repair_worker) ──────────────

def _worker(tmp_path):
    from core.repair_worker import RepairWorker
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "music.db"))
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    Path(w.transfer_folder).mkdir(exist_ok=True)
    return w


def test_approving_a_finding_applies_exactly_the_promised_rename(tmp_path):
    """The reporter's live test: approving a finding must perform the rename the
    finding SHOWED (DDTT rebuilt), not recompute a 3-digit splice — and a
    filename-only finding (tag_ok) must not rewrite a correct tag."""
    w = _worker(tmp_path)
    f = Path(w.transfer_folder) / "0113 - Thirteen Lights.flac"
    _make_flac(f, {'title': 'Thirteen Lights', 'tracknumber': '13/14', 'discnumber': '2/3'})

    details = {'correct_track_num': 13, 'total_tracks': 14, 'tag_ok': True,
               'disc_number': 2, 'new_filename': '0213 - Thirteen Lights.flac'}
    res = w._fix_track_number('file', None, str(f), details)
    assert res['success'] is True

    renamed = Path(w.transfer_folder) / "0213 - Thirteen Lights.flac"
    assert renamed.is_file() and not f.exists()
    assert FLAC(str(renamed))['tracknumber'] == ['13/14']   # tag untouched (tag_ok)


def test_approving_a_legacy_finding_never_mangles_a_ddtt_name(tmp_path):
    """Findings created BEFORE the plan rode along carry no new_filename. The
    conservative rebuild fixes plain prefixes but leaves 4-digit disc+track
    names alone — the old splice ('0213' -> '133') is dead on this path too."""
    w = _worker(tmp_path)
    f = Path(w.transfer_folder) / "0213 - Thirteen Lights.flac"
    _make_flac(f, {'title': 'Thirteen Lights', 'tracknumber': '1/40', 'discnumber': '2/3'})

    legacy = {'correct_track_num': 13, 'total_tracks': 40}   # old finding shape
    res = w._fix_track_number('file', None, str(f), legacy)
    assert res['success'] is True
    assert f.is_file()                                       # filename untouched
    assert FLAC(str(f))['tracknumber'] == ['13/40']          # tag still corrected

    # …while a legacy finding on a plain 2-digit prefix still renames it
    g = Path(w.transfer_folder) / "01 - Beta Song.flac"
    _make_flac(g, {'title': 'Beta Song', 'tracknumber': '1/3'})
    res2 = w._fix_track_number('file', None, str(g),
                               {'correct_track_num': 2, 'total_tracks': 3})
    assert res2['success'] is True
    assert (Path(w.transfer_folder) / "02 - Beta Song.flac").is_file()
    assert not g.exists()
