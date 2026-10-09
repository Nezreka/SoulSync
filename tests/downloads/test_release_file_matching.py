"""Real tagged audio regressions for release extraction and album track pairing."""

import shutil
import subprocess
from pathlib import Path
import pytest
from core.downloads.release_import import read_release_file, select_requested_file, match_album_tracks
from core.imports.file_integrity import check_audio_integrity


@pytest.fixture
def audio(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required to build test audio")
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


@pytest.mark.parametrize("seconds,expected_ms", [(2, 12000), (18, 2000)])
def test_duration_rejects_different_recording(audio, seconds, expected_ms):
    item = audio("Artist - Song.flac", "Song", seconds=seconds)
    assert select_requested_file([item], track(duration=expected_ms)) == item
    assert not check_audio_integrity(item.path, expected_ms).ok


def test_duplicate_recording_candidates_are_ambiguous(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Song")]
    assert select_requested_file(items, track()) is None


def test_same_path_reported_twice_is_not_ambiguous(audio):
    item = audio("a.flac", "Song")
    assert select_requested_file([item, item], track()) == item


def test_requested_position_settles_one_title_on_two_discs(audio):
    items = [audio("cd1.flac", "Intro", track=1, disc=1), audio("cd2.flac", "Intro", track=1, disc=2)]
    assert select_requested_file(items, track("Intro", 1, 2)).path == items[1].path


def test_profile_format_settles_a_flac_and_mp3_release(audio):
    items = [audio("Song.flac", "Song"), audio("Song.mp3", "Song")]
    assert select_requested_file(items, track(), ["flac"]).path == items[0].path
    assert select_requested_file(items, track(), ["mp3", "flac"]).path == items[1].path
    assert select_requested_file(items, track()) is None


def test_album_pairs_every_file_once_including_multiple_discs(audio):
    items = [audio("disc1.flac", "Song", track=1, disc=1), audio("disc2.flac", "Other", track=1, disc=2)]
    assert len(match_album_tracks(items, [track(), track("Other", 1, 2)], "Album")) == 2


def test_partial_release_pairs_the_tracks_it_has(audio):
    pairs = match_album_tracks([audio("a.flac", "Song")], [track(), track("Other", 2)], "Album")
    assert [entry[0]["name"] for entry in pairs] == ["Song"]


def test_other_edition_files_are_not_paired(audio):
    items = [audio("a.flac", "Song", album="Album (Deluxe)"), audio("b.flac", "Other", album="Album (Deluxe)", track=2)]
    assert match_album_tracks(items, [track(), track("Other", 2)], "Album") == []


def test_file_at_another_position_is_not_paired(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Other", track=1)]
    assert [entry[0]["name"] for entry in match_album_tracks(items, [track(), track("Other", 2)], "Album")] == ["Song"]


def test_duet_credit_matches_primary_artist_without_losing_collaborators(audio):
    item = audio("duet.flac", "Under Pressure", artist="Queen & David Bowie")
    wanted = dict(track("Under Pressure"), artists=[{"name": "Queen"}, {"name": "David Bowie"}])
    assert select_requested_file([item], wanted) == item


def test_missing_catalogue_artist_leaves_that_track_unpaired(audio):
    items = [audio("a.flac", "Song"), audio("b.flac", "Other", artist="Someone Else", track=2)]
    missing = dict(track("Other", 2), artists=[])
    assert [entry[0]["name"] for entry in match_album_tracks(items, [track(), missing], "Album")] == ["Song"]
