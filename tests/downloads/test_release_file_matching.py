"""Real tagged audio regressions for release extraction and completeness."""

import subprocess
from pathlib import Path
import pytest
from core.downloads.release_import import read_release_file, select_requested_file, match_complete_album


@pytest.fixture
def audio(tmp_path):
    def create(filename, title, artist="Artist", album="Album", track=1, disc=1, seconds=2):
        path = tmp_path / filename
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds}",
                "-metadata",
                f"title={title}",
                "-metadata",
                f"artist={artist}",
                "-metadata",
                f"album={album}",
                "-metadata",
                f"track={track}",
                "-metadata",
                f"disc={disc}",
                "-y",
                str(path),
            ],
            check=True,
        )
        return read_release_file(str(path))

    return create


def track(name="Song", number=1, disc=1, duration=2000):
    return {"id": str(number), "name": name, "artists": [{"name": "Artist"}], "track_number": number, "disc_number": disc, "duration_ms": duration}


def test_tags_find_song_in_opaque_scene_filename(audio):
    item = audio("obfuscated.flac", "Song")
    assert select_requested_file([item], track()).path == item.path


def test_wrong_artist_tags_cannot_be_overridden_by_filename(audio):
    item = audio("Artist - Song.flac", "Song", artist="Someone Else")
    assert select_requested_file([item], track()) is None


def test_unrequested_recording_version_is_rejected(audio):
    item = audio("Artist - Song.flac", "Song (Live)")
    assert select_requested_file([item], track()) is None


def test_duration_rejects_different_recording(audio):
    item = audio("Artist - Song.flac", "Song", seconds=12)
    assert select_requested_file([item], track()) is None


def test_duplicate_recording_candidates_are_ambiguous(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Song")]
    assert select_requested_file(items, track()) is None


def test_same_path_reported_twice_is_not_ambiguous(audio):
    item = audio("a.flac", "Song")
    assert select_requested_file([item, item], track()) == item


def test_album_matches_every_file_once_including_multiple_discs(audio):
    items = [audio("disc1.flac", "Song", track=1, disc=1), audio("disc2.flac", "Other", track=1, disc=2)]
    result = match_complete_album(items, [track(), track("Other", 1, 2)], "Album")
    assert result and len(result) == 2


def test_incomplete_album_is_not_expanded(audio):
    assert match_complete_album([audio("a.flac", "Song")], [track(), track("Other", 2)], "Album") is None


def test_wrong_edition_is_not_expanded(audio):
    items = [audio("a.flac", "Song", album="Album (Deluxe)"), audio("b.flac", "Other", album="Album (Deluxe)", track=2)]
    assert match_complete_album(items, [track(), track("Other", 2)], "Album") is None


def test_duplicate_track_slots_are_not_expanded(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Other", track=1)]
    assert match_complete_album(items, [track(), track("Other", 2)], "Album") is None


def test_duet_credit_matches_primary_artist_without_losing_collaborators(audio):
    item = audio("duet.flac", "Under Pressure", artist="Queen & David Bowie")
    wanted = dict(track("Under Pressure"), artists=[{"name": "Queen"}, {"name": "David Bowie"}])
    assert select_requested_file([item], wanted) == item


def test_missing_catalogue_artist_refuses_bonus_instead_of_retagging(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Other", artist="Someone Else", track=2)]
    missing = dict(track("Other", 2), artists=[])
    assert match_complete_album(items, [track(), missing], "Album") is None
