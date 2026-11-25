"""the Soulseek scorer, on the recordings it got wrong.

from a review of the download side (Nov 24 2026). each case was run through
the real scorer first: anything over 0.58 is downloaded.

accepted that should not have been:
  a tribute act for the artist it is named after (1.00), the studio track
  when the live one was asked for (0.71), other tracks of a self-titled
  album (0.67, 0.69), a ten-minute cut of a four-minute song (0.66).
rejected that should not have been (0.00):
  titles and albums whose NAME holds a version word ("Live Forever",
  "Stripped", "Tour de France"), and compilations filed under
  "Various Artists".
"""

from types import SimpleNamespace as NS

import pytest

from core.matching_engine import MusicMatchingEngine

BAR = 0.58
engine = MusicMatchingEngine()


def want(name, artist, album="", seconds=240):
    return NS(id="x", name=name, artists=[artist], album=album, duration_ms=seconds * 1000, popularity=0)


def cand(filename, seconds=240):
    return NS(username="peer", filename=filename, size=30_000_000, bitrate=None,
              duration=seconds * 1000 if seconds else None, quality="flac", free_upload_slots=1,
              upload_speed=1000, queue_length=0, artist=None, title=None, album=None,
              track_number=None, sample_rate=None, bit_depth=None)


def score(w, c):
    return engine.calculate_slskd_match_confidence_enhanced(w, c)[0]


@pytest.mark.parametrize("label,w,c", [
    ("a tribute act", want("Bohemian Rhapsody", "Queen", "Greatest Hits", 354),
     cand("Queen Tribute Band\\Greatest Hits\\01 - Bohemian Rhapsody.flac", 354)),
    ("a karaoke act", want("Bohemian Rhapsody", "Queen", "", 354),
     cand("Karaoke Hits - In the Style of Queen\\01 - Bohemian Rhapsody.flac", 354)),
    ("the studio track when live was asked for", want("Wonderwall (Live)", "Oasis", "", 258),
     cand("Oasis\\(What's The Story) Morning Glory\\03 - Wonderwall.flac", 270)),
    ("another track of a self-titled album", want("Paranoid", "Black Sabbath", "Paranoid", 170),
     cand("Black Sabbath\\Paranoid\\02 - Planet Caravan.flac", 272)),
    ("and another", want("Paranoid", "Black Sabbath", "Paranoid", 170),
     cand("Black Sabbath\\Paranoid\\04 - Iron Man.flac", 355)),
    ("a ten-minute cut of a four-minute song", want("Blue Monday", "New Order", "", 240),
     cand("New Order\\Singles\\Blue Monday.flac", 600)),
    ("a half-length clip", want("Blue Monday", "New Order", "", 240),
     cand("New Order\\Singles\\Blue Monday.flac", 110)),
    ("a real live cut when the studio track was asked for", want("Wonderwall", "Oasis", "", 258),
     cand("Oasis\\Live at Knebworth\\05 - Wonderwall.flac", 262)),
])
def test_the_wrong_recording_is_turned_away(label, w, c):
    assert score(w, c) < BAR, label


@pytest.mark.parametrize("label,w,c", [
    ("the right track of a self-titled album", want("Paranoid", "Black Sabbath", "Paranoid", 170),
     cand("Black Sabbath\\Paranoid\\03 - Paranoid.flac", 170)),
    ("a title that holds the word live", want("Live Forever", "Oasis", "Definitely Maybe", 276),
     cand("Oasis\\Definitely Maybe\\03 - Live Forever.flac", 276)),
    ("live and let die", want("Live and Let Die", "Wings", "", 192),
     cand("Wings\\Greatest\\08 - Live and Let Die.flac", 192)),
    ("an album called Stripped", want("Beautiful", "Christina Aguilera", "Stripped", 238),
     cand("Christina Aguilera\\Stripped\\11 - Beautiful.flac", 238)),
    ("an album called Live Through This", want("Violet", "Hole", "Live Through This", 204),
     cand("Hole\\Live Through This\\01 - Violet.flac", 204)),
    ("tour de france", want("Tour de France", "Kraftwerk", "Tour de France", 312),
     cand("Kraftwerk\\Tour de France\\07 - Tour de France.flac", 312)),
    ("a compilation filed under various artists", want("Take On Me", "a-ha", "", 225),
     cand("Various Artists\\Now 80s\\05 - a-ha - Take On Me.flac", 225)),
    ("the live cut that was asked for", want("Wonderwall (Live)", "Oasis", "", 258),
     cand("Oasis\\Live at Knebworth\\05 - Wonderwall (Live).flac", 258)),
    ("a live album's own file, not marked live", want("Wonderwall (Live)", "Oasis", "Familiar to Millions", 258),
     cand("Oasis\\Familiar to Millions\\05 - Wonderwall.flac", 258)),
    ("a few seconds' difference", want("Yesterday", "The Beatles", "Help!", 125),
     cand("The Beatles\\Help!\\13 - Yesterday.flac", 131)),
    ("the artist's own album with orchestra in its name", want("One", "Metallica", "S&M", 470),
     cand("Metallica\\S&M (with the San Francisco Symphony Orchestra)\\09 - One.flac", 470)),
    ("no length on the peer's side", want("Yesterday", "The Beatles", "Help!", 125),
     cand("The Beatles\\Help!\\13 - Yesterday.flac", 0)),
])
def test_the_right_recording_is_taken(label, w, c):
    assert score(w, c) >= BAR, label


def test_a_compilation_folder_without_the_artist_in_the_file_name_is_still_turned_away():
    assert score(want("Take On Me", "a-ha", "", 225),
                 cand("Various Artists\\Now 80s\\05 - Take On Me.flac", 225)) < BAR


@pytest.mark.parametrize("title,asked", [
    ("Wonderwall (Live)", "live"), ("Wonderwall - Live at Knebworth", "live"),
    ("Song (Acoustic)", "acoustic"), ("Song (Skrillex Remix)", "remix"),
    ("Song - Instrumental", "instrumental"),
    ("Live Forever", None), ("Live and Let Die", None), ("Remix to Ignition", None),
    ("Acoustic Guitar Blues", None), ("Saturday Night Live", None),
])
def test_a_title_asks_for_a_version_only_where_a_qualifier_sits(title, asked):
    assert engine._title_asks_for_version(title) == asked


@pytest.mark.parametrize("wanted,candidate,other", [
    (240_000, 600_000, True), (240_000, 110_000, True), (240_000, 5_000, True),
    (240_000, 252_000, False), (240_000, 230_000, False), (240_000, 0, False), (0, 240_000, False),
    (240_000, 240, False),          # the same length, given in seconds
    (30 * 60_000, 33 * 60_000, False),   # ten percent on a long piece
])
def test_a_length_far_off_is_another_recording(wanted, candidate, other):
    assert MusicMatchingEngine.duration_is_another_recording(wanted, candidate) is other


def test_a_marked_extended_mix_is_left_to_the_version_rules():
    """it says what it is: the preference for (or against) extended versions
    decides, not the length gate"""
    assert score(want("Blue Monday", "New Order", "", 240),
                 cand("New Order\\Singles\\Blue Monday (Extended Mix).flac", 450)) >= BAR


@pytest.mark.parametrize("a,b,same", [
    ("Hotel California - Live; 1994 Remaster", "Hotel California", False),
    ("Hotel California (Live) [Remastered]", "Hotel California", False),
    ("Song - Acoustic, 2011 Remaster", "Song", False),
    ("Song - 2012 Remaster", "Song", True),
    ("Song (Remastered 2009)", "Song", True),
])
def test_a_remastered_live_cut_is_not_the_studio_track(a, b, same):
    """the remaster word was checked first and returned 0.75, a match, before
    the live word was looked at"""
    value = engine.similarity_score(engine.normalize_string(a), engine.normalize_string(b))
    assert (value >= 0.70) is same, value
