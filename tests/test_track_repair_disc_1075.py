"""Track Number Repair vs album versions + disc tags (#1075, Lain2077).

KID A MNESIA: 3 discs, and disc 3 opens with "Like Spinning Plates
('Why Us?' Version)" while disc 2 closes with the original "Like Spinning
Plates". The matcher strips parentheses before comparing, so BOTH candidates
normalized to the same string, both scored 100%, and first-in-tracklist won:
the file was matched to the wrong disc's original version and renumbered
1 → 10. On top of that the repair wrote per-disc track numbers but never the
disc tag — on a disc-tagless file that just manufactures a duplicate track
number — and the finding never said the numbering was disc-relative.

Pins: raw-title tiebreak on stripped-score ties, disc-tag enforcement on
multi-disc albums (never on single-disc), explicit disc wording in the
changes, and approve-path parity for the disc write.
"""

from __future__ import annotations

import struct
from pathlib import Path

from mutagen.flac import FLAC

from core.repair_jobs.track_number_repair import (
    _fix_disc_number_tag,
    _match_title_to_api_track,
    _check_single_track,
    _plan_track_repair,
)


def _make_flac(path: Path, tags: dict | None = None) -> None:
    fLaC = b'fLaC'
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    block_header = bytes([0x80, 0x00, 0x00, 0x22])
    path.write_bytes(fLaC + block_header + bytes(streaminfo))
    audio = FLAC(str(path))
    if tags:
        for k, v in tags.items():
            audio[k] = [v]
    audio.save()


def _kid_a_mnesia():
    """The reporter's album shape: 3 discs; disc 2 ends with the original
    'Like Spinning Plates' (#10 of 11), disc 3 opens with the 'Why Us?'
    version (#1 of 12)."""
    tracks = []
    for n in range(1, 12):
        name = 'Like Spinning Plates' if n == 10 else f'D2 Song {n:02d}'
        tracks.append({'name': name, 'track_number': n, 'disc_number': 2})
    tracks.append({'name': "Like Spinning Plates ('Why Us?' Version)",
                   'track_number': 1, 'disc_number': 3})
    for n in range(2, 13):
        tracks.append({'name': f'D3 Song {n:02d}', 'track_number': n, 'disc_number': 3})
    for n in range(1, 12):
        tracks.insert(n - 1, {'name': f'D1 Song {n:02d}', 'track_number': n,
                              'disc_number': 1})
    return tracks


# ── the version tiebreak ─────────────────────────────────────────────────────

def test_version_qualifier_breaks_normalized_ties():
    """The reporter's exact case: the 'Why Us?' version file must match the
    'Why Us?' API track (D3 #1), not the original (D2 #10) that used to win
    by tracklist order."""
    m, score = _match_title_to_api_track(
        "Like Spinning Plates ('Why Us?' Version)", _kid_a_mnesia(), 0.8)
    assert m['disc_number'] == 3 and m['track_number'] == 1
    assert score == 1.0

    # and the mirror: the plain original still matches the original
    m2, _ = _match_title_to_api_track('Like Spinning Plates', _kid_a_mnesia(), 0.8)
    assert m2['disc_number'] == 2 and m2['track_number'] == 10


def test_tiebreak_never_outranks_a_better_stripped_score():
    """The raw title only breaks TIES — a clearly better stripped match must
    still win even when its raw similarity is lower."""
    tracks = [
        {'name': 'Karma Police (Live at Oxford)', 'track_number': 1, 'disc_number': 1},
        {'name': 'Karma Poliz', 'track_number': 2, 'disc_number': 1},
    ]
    m, _ = _match_title_to_api_track('Karma Police', tracks, 0.5)
    assert m['track_number'] == 1          # stripped: exact; raw penalty ignored


def test_full_plan_lands_on_the_right_disc(tmp_path):
    """End to end on the reporter's file: no disc tag, track #1 — the plan
    must match D3 #1 (already-correct number) and propose ONLY the missing
    disc tag, not a renumber to 10."""
    f = tmp_path / "Radiohead - Like Spinning Plates ('Why Us_' Version).flac"
    _make_flac(f, {'title': "Like Spinning Plates ('Why Us?' Version)",
                   'tracknumber': '1'})
    plan = _plan_track_repair(str(f), f.name, _kid_a_mnesia(), 0.8)
    assert plan is not None
    assert plan['correct_disc'] == 3 and plan['correct_num'] == 1
    assert plan['tag_ok'] is True          # 1 was right all along
    assert plan['disc_ok'] is False        # the missing disc tag IS the finding
    assert plan['total_discs'] == 3


# ── disc-tag enforcement ─────────────────────────────────────────────────────

def test_multi_disc_file_without_disc_tag_is_flagged(tmp_path):
    f = tmp_path / "10 - Like Spinning Plates.flac"
    _make_flac(f, {'title': 'Like Spinning Plates', 'tracknumber': '10/11'})
    finding = _check_single_track(str(f), f.name, _kid_a_mnesia(), 0.8)
    assert finding is not None
    assert any(c == 'Disc: none -> 2/3' for c in finding['details']['changes'])
    assert finding['details']['disc_ok'] is False
    assert finding['details']['disc_number'] == 2
    assert finding['details']['total_discs'] == 3


def test_wrong_disc_tag_is_corrected(tmp_path):
    f = tmp_path / "10 - Like Spinning Plates.flac"
    _make_flac(f, {'title': 'Like Spinning Plates', 'tracknumber': '10/11',
                   'discnumber': '1'})
    plan = _plan_track_repair(str(f), f.name, _kid_a_mnesia(), 0.8)
    # NB: a wrong disc tag steers disc-aware matching first, but the full-
    # tracklist fallback still finds the right track; the disc gets fixed
    assert plan is not None and plan['disc_ok'] is False
    assert plan['correct_disc'] == 2


def test_correct_multi_disc_file_stays_quiet(tmp_path):
    f = tmp_path / "10 - Like Spinning Plates.flac"
    _make_flac(f, {'title': 'Like Spinning Plates', 'tracknumber': '10/11',
                   'discnumber': '2/3'})
    assert _plan_track_repair(str(f), f.name, _kid_a_mnesia(), 0.8) is None


def test_single_disc_albums_never_get_disc_findings(tmp_path):
    tracks = [{'name': f'Song {n}', 'track_number': n, 'disc_number': 1}
              for n in range(1, 11)]
    f = tmp_path / "03 - Song 3.flac"
    _make_flac(f, {'title': 'Song 3', 'tracknumber': '3/10'})   # no disc tag
    assert _plan_track_repair(str(f), f.name, tracks, 0.8) is None


def test_track_change_wording_is_disc_relative(tmp_path):
    """The confusion in #1075: '1 -> 10' with no hint the 10 is disc-local.
    Multi-disc changes must say the disc."""
    f = tmp_path / "Radiohead - Like Spinning Plates.flac"
    _make_flac(f, {'title': 'Like Spinning Plates', 'tracknumber': '1'})
    finding = _check_single_track(str(f), f.name, _kid_a_mnesia(), 0.8)
    assert finding is not None
    changes = finding['details']['changes']
    assert any(c.startswith('Track number: 1 -> 10 (disc 2 of 3)') for c in changes)
    assert any('per disc' in c for c in changes if c.startswith('Total tracks'))


# ── the disc writer + approve-path parity ────────────────────────────────────

def test_fix_disc_number_tag_writes_flac_tags(tmp_path):
    f = tmp_path / "x.flac"
    _make_flac(f, {'title': 'X'})
    _fix_disc_number_tag(str(f), 2, 3)
    audio = FLAC(str(f))
    assert audio['discnumber'] == ['2/3']
    assert audio['disctotal'] == ['3']


def test_approve_path_honors_disc_ok():
    """The worker's approval fixer must (a) write the disc tag when the
    finding says disc_ok False, and (b) never disc-write on legacy findings
    that predate the field."""
    import inspect
    from core import repair_worker
    src = inspect.getsource(repair_worker)
    assert "details.get('disc_ok', True)" in src
    assert '_fix_disc_number_tag' in src
