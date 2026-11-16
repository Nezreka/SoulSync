"""#2134: numbered releases with near identical titles got crossed.

above & beyond's group therapy episodes are 'Group Therapy 696 (DJ Mix)',
'Group Therapy 699 (DJ Mix)' and so on, and every episode carries the same
shaped tracks: 'Group Therapy Intro (ABGT696) (Mixed)', 'Group Therapy
(Messages Pt. 1) [ABGT696] (Mixed)' to 'Pt. 6'. the titles below are the real
deezer tracklist of episode 696.

the download matcher scored the Pt. 4 file 0.97 for the Pt. 1 slot (a
streaming hit tied the right one at 1.0), so two slots of one episode got the
same audio, and a file from another episode scored as high as the right one.
"""

from types import SimpleNamespace

import pytest

import core.matching_engine as me
from core.downloads.soulseek_identity import assign_album_tracks, match_track
from core.matching_engine import MusicMatchingEngine
from core.text.title_match import numbers_conflict

ARTIST = "Above & Beyond"
PT1 = "Group Therapy (Messages Pt. 1) [ABGT696] (Mixed)"
PT4 = "Group Therapy (Messages Pt. 4) [ABGT696] (Mixed)"
INTRO_696 = "Group Therapy Intro (ABGT696) (Mixed)"
INTRO_699 = "Group Therapy Intro (ABGT699) (Mixed)"


class _Config:
    def get(self, key, default=None):
        return default


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch):
    monkeypatch.setattr(me, "config_manager", _Config())


@pytest.fixture
def engine():
    return MusicMatchingEngine()


def _wanted(name, album, duration_ms=44000):
    return SimpleNamespace(name=name, artists=[ARTIST], album=album,
                           duration_ms=duration_ms)


def _hit(filename, *, album=None, duration_ms=45000):
    streaming = "||" in filename
    return SimpleNamespace(
        filename=filename, username="tidal" if streaming else "peer",
        duration=duration_ms, quality="flac", bitrate=None, size=1,
        album=album, artist=ARTIST)


# --- the shared rule ---------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Group Therapy 696", "Group Therapy 699"),
    (PT1, PT4),
    (INTRO_696, INTRO_699),
    ("Now 99", "Now 100"),
    ("Vol. 1", "Vol. 2"),
])
def test_numbers_that_disagree_conflict(a, b):
    assert numbers_conflict(a, b)
    assert numbers_conflict(b, a)


@pytest.mark.parametrize("a,b", [
    ("Group Therapy 696", "Group Therapy 696 (DJ Mix)"),
    ("Song", "Song 2"),                                   # one side has none
    ("Song - 2011 Remaster", "Song (Remastered 2009)"),   # years are dates
    ("Symphony No. 9", "Symphony No. 9, Op. 125"),        # extra numbers
    (PT1, "03 - " + PT1 + " [24-96]"),                    # file name noise
    ("ABGT696", "Group Therapy 696"),                     # digit runs
    ("Pt. 01", "Pt. 1"),
    ("1989", "1989 (Deluxe)"),
])
def test_numbers_that_agree_or_are_missing_do_not(a, b):
    assert not numbers_conflict(a, b)


# --- per track download matching --------------------------------------------

def test_messages_part_four_file_is_not_part_one(engine):
    """one file must not answer two slots: Pt. 4 is not Pt. 1 (#2134)."""
    want = _wanted(PT1, "Group Therapy 696 (DJ Mix)")
    wrong = _hit(f"{ARTIST}/Group Therapy 696 (DJ Mix)/09 - {PT4}.flac")
    right = _hit(f"{ARTIST}/Group Therapy 696 (DJ Mix)/03 - {PT1}.flac")
    assert engine.calculate_slskd_match_confidence_enhanced(want, wrong)[0] == 0.0
    assert engine.calculate_slskd_match_confidence_enhanced(want, right)[0] > 0.63


def test_streaming_hit_for_another_part_is_rejected(engine):
    """a streaming hit for Pt. 4 tied the right one at 1.0 (#2134)."""
    want = _wanted(PT1, "Group Therapy 696 (DJ Mix)")
    wrong = _hit(f"123||{ARTIST} - {PT4}", album="Group Therapy 696 (DJ Mix)")
    right = _hit(f"124||{ARTIST} - {PT1}", album="Group Therapy 696 (DJ Mix)")
    assert engine.calculate_slskd_match_confidence_enhanced(want, wrong)[0] == 0.0
    assert engine.calculate_slskd_match_confidence_enhanced(want, right)[0] > 0.63


def test_another_episodes_track_is_rejected(engine):
    want = _wanted(INTRO_699, "Group Therapy 699 (DJ Mix)", 30000)
    wrong = _hit(f"{ARTIST}/Group Therapy 696 (DJ Mix)/01 - {INTRO_696}.flac",
                 duration_ms=30000)
    assert engine.calculate_slskd_match_confidence_enhanced(want, wrong)[0] == 0.0


def test_another_episodes_folder_earns_no_album_bonus(engine):
    """an untagged 'Group Therapy Intro' in the 696 folder got the same +0.10
    album bonus as the one in the 699 folder (#2134)."""
    want = _wanted(INTRO_699, "Group Therapy 699 (DJ Mix)", 30000)
    other = _hit(f"{ARTIST}/Group Therapy 696 (DJ Mix) [2026]/01 - Group Therapy Intro.flac",
                 duration_ms=30000)
    own = _hit(f"{ARTIST}/Group Therapy 699 (DJ Mix) [2026]/01 - Group Therapy Intro.flac",
               duration_ms=30000)
    engine.calculate_slskd_match_confidence(want, other)
    engine.calculate_slskd_match_confidence(want, own)
    assert other.album_match == 0.0
    assert own.album_match == 0.10
    assert engine.album_field_match("Group Therapy 699 (DJ Mix)",
                                    "Group Therapy 696 (DJ Mix)") == 0.0


# --- soulseek album folders ---------------------------------------------------

def test_other_episode_file_is_no_weak_edge():
    result = match_track({"name": INTRO_699},
                         {"filename": f"x/01 - {INTRO_696}.flac"})
    assert not result.matches
    assert result.title_similarity < 0.80


def test_other_episode_folder_does_not_cover_the_wanted_one():
    """a 696 folder covered every same shaped 699 slot through weak edges."""
    wanted = [{"name": t} for t in (
        INTRO_699, "Group Therapy (Messages Pt. 1) [ABGT699] (Mixed)",
        "Group Therapy (Messages Pt. 2) [ABGT699] (Mixed)")]
    files = [{"filename": f"x/{i:02d} - {t}.flac"} for i, t in enumerate((
        INTRO_696, PT1, "Group Therapy (Messages Pt. 2) [ABGT696] (Mixed)"), 1)]
    assert assign_album_tracks(wanted, files).coverage == 0.0
