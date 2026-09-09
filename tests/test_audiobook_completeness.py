"""Tests for core/audiobook_completeness.py.

A download client says "complete" when the files it was ASKED for finished,
which is not the same as the book being whole. A book missing its last chapters
plays perfectly right up until the listener runs out of it, so this is the gate
between the client's opinion and the library.

Durations are stubbed; no test reads a real audio file.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.audiobook_completeness import (
    DEFAULT_TOLERANCE,
    assess,
    collect_audio,
    staging_days_from_settings,
    staging_expired,
    tolerance_from_settings,
)


def _book_folder(tmp_path, names, size=5 * 1024 * 1024):
    folder = tmp_path / "download"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x" * size)
    return folder


def _durations(mapping):
    """Patch the per-file duration reader from a {filename: seconds} map."""
    def fake(path):
        return mapping.get(Path(path).name)
    return patch("core.audiobook_completeness.measure_duration_seconds", side_effect=fake)


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------

def test_audio_files_are_found_recursively(tmp_path):
    folder = _book_folder(tmp_path, ["01.mp3", "02.mp3"])
    (folder / "Disc 2").mkdir()
    (folder / "Disc 2" / "03.mp3").write_bytes(b"x")
    assert len(collect_audio(folder)) == 3


def test_non_audio_is_ignored(tmp_path):
    folder = _book_folder(tmp_path, ["01.mp3", "cover.jpg", "notes.txt"])
    assert [p.name for p in collect_audio(folder)] == ["01.mp3"]


def test_a_missing_folder_has_no_audio(tmp_path):
    assert collect_audio(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_a_whole_book_passes(tmp_path):
    # 10 hours expected, 10 hours present.
    folder = _book_folder(tmp_path, [f"{i:02d}.mp3" for i in range(10)])
    with _durations({f"{i:02d}.mp3": 3600.0 for i in range(10)}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["complete"] is True
    assert verdict["measured_minutes"] == 600.0


def test_a_book_missing_chapters_is_held(tmp_path):
    # The failure this whole module exists for: seven of ten hours present,
    # every file playing perfectly.
    folder = _book_folder(tmp_path, [f"{i:02d}.mp3" for i in range(7)])
    with _durations({f"{i:02d}.mp3": 3600.0 for i in range(7)}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["complete"] is False
    assert "short" in verdict["reason"]
    assert verdict["ratio"] == pytest.approx(0.7, abs=0.01)


def test_a_few_seconds_short_still_passes(tmp_path):
    # Audible's runtime includes publisher intros and rounds to the minute; a
    # rip that drops a little silence is still the whole book.
    folder = _book_folder(tmp_path, ["01.mp3"])
    with _durations({"01.mp3": 590 * 60.0}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["complete"] is True


def test_the_tolerance_is_the_line(tmp_path):
    folder = _book_folder(tmp_path, ["01.mp3"])
    just_under = (DEFAULT_TOLERANCE - 0.02) * 600 * 60
    with _durations({"01.mp3": just_under}):
        assert assess(str(folder), 600)["complete"] is False
    just_over = (DEFAULT_TOLERANCE + 0.02) * 600 * 60
    with _durations({"01.mp3": just_over}):
        assert assess(str(folder), 600)["complete"] is True


def test_a_folder_holding_a_whole_series_is_rejected(tmp_path):
    # Far over the runtime means this is not one book.
    folder = _book_folder(tmp_path, ["01.mp3"])
    with _durations({"01.mp3": 600 * 60.0 * 5}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["complete"] is False
    assert "more than one title" in verdict["reason"]


def test_a_modest_overshoot_is_fine(tmp_path):
    # Duplicated intros and bonus interviews overshoot legitimately.
    folder = _book_folder(tmp_path, ["01.mp3"])
    with _durations({"01.mp3": 600 * 60.0 * 1.2}):
        assert assess(str(folder), expected_minutes=600)["complete"] is True


def test_an_empty_download_is_never_complete(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    verdict = assess(str(folder), expected_minutes=600)
    assert verdict["complete"] is False
    assert "No audio files" in verdict["reason"]


@pytest.mark.parametrize("expected", [0, None, -5])
def test_with_no_published_runtime_there_is_nothing_to_check(tmp_path, expected):
    # A book we know nothing about is let through rather than held forever on a
    # comparison that cannot be made.
    folder = _book_folder(tmp_path, ["01.mp3"])
    with _durations({"01.mp3": 60.0}):
        verdict = assess(str(folder), expected_minutes=expected)
    assert verdict["complete"] is True
    assert "No published runtime" in verdict["reason"]


# ---------------------------------------------------------------------------
# Unreadable files
# ---------------------------------------------------------------------------

def test_an_unreadable_file_is_not_counted_as_silence(tmp_path):
    # Counting it as zero would make a complete book look short and strand it.
    folder = _book_folder(tmp_path, ["01.mp3", "02.mp3"], size=300 * 1024 * 1024)
    with _durations({"01.mp3": 300 * 60.0, "02.mp3": None}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["unreadable"] == 1
    assert verdict["measured_by"] == "duration+size"
    assert verdict["complete"] is True


def test_a_wholly_unreadable_download_falls_back_to_size(tmp_path):
    # An exotic codec must not strand a good book.
    folder = _book_folder(tmp_path, ["01.m4b"], size=400 * 1024 * 1024)
    with _durations({"01.m4b": None}):
        verdict = assess(str(folder), expected_minutes=600)
    assert verdict["measured_by"] == "size"
    assert verdict["complete"] is True


def test_the_weaker_check_is_reported(tmp_path):
    # The caller should know the verdict came from bytes, not playing time.
    folder = _book_folder(tmp_path, ["01.m4b"])
    with _durations({"01.m4b": None}):
        assert assess(str(folder), 600)["measured_by"] == "size"


# ---------------------------------------------------------------------------
# Staging deadline
# ---------------------------------------------------------------------------

def test_a_book_is_given_time(tmp_path):
    import time

    assert staging_expired(time.time() - 3600, days=7) is False


def test_a_book_is_not_staged_forever():
    import time

    # A broken release would otherwise hold a wishlist row for good.
    assert staging_expired(time.time() - 8 * 86400, days=7) is True


def test_a_zero_deadline_means_wait_indefinitely():
    assert staging_expired(0, days=0) is False


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_the_tolerance_default_is_forgiving_but_real():
    assert 0.85 <= tolerance_from_settings() <= 0.98


@pytest.mark.parametrize("value", [0, 1.5, -1, "nonsense", None])
def test_a_nonsense_tolerance_falls_back(value):
    # 0 would import anything; above 1 could never be satisfied.
    with patch("core.settings.config_manager.get", return_value=value):
        assert tolerance_from_settings() == DEFAULT_TOLERANCE


def test_the_staging_window_can_be_configured():
    with patch("core.settings.config_manager.get", return_value=3):
        assert staging_days_from_settings() == 3.0


def test_a_negative_staging_window_is_clamped():
    with patch("core.settings.config_manager.get", return_value=-5):
        assert staging_days_from_settings() == 0.0
