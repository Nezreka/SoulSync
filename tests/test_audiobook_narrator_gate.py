"""Narrator-aware audiobook completeness: the edition's runtime must not gate
a download that is (or may be) a different narration.

Repro: SeadogsBooty's Silmarillion — wishlist ASIN is the Andy Serkis edition
(19h24m = 1164 min), the grabbed release "J.R.R. Tolkien - The Silmarillion"
names no narrator, and the files hold ~886 min (the Martin Shaw reading,
14h46m, complete). The old code held it: "Only about 886 of 1164 minutes
present — about 277 minutes short".
"""

from core import audiobook_completeness as completeness
from core import audiobook_download_monitor as monitor


SERKIS_EDITION = {
    "asin": "B0SERKIS",
    "title": "The Silmarillion",
    "author_names": ["J. R. R. Tolkien"],
    "narrator_names": ["Andy Serkis"],
    "runtime_minutes": 1164,
}


def _row(**overrides):
    payload = {
        "download_id": "d-silmarillion",
        "asin": "B0SERKIS",
        "title": "The Silmarillion",
        "author": "J. R. R. Tolkien",
        "source": "torrent",
        "status": "importing",
    }
    payload.update(overrides)
    return payload


def _check(monkeypatch, tmp_path, *, release_title, tag_narrators,
           measured_minutes, edition=None):
    """Run the real _check_complete with faked audio on disk."""
    edition = dict(SERKIS_EDITION if edition is None else edition)
    files = []
    for i in range(3):
        path = tmp_path / f"chapter{i + 1:02d}.mp3"
        path.write_bytes(b"\x00" * 64)
        files.append(path)
    per_file = measured_minutes * 60.0 / len(files)
    monkeypatch.setattr(completeness, "collect_audio", lambda folder: list(files))
    monkeypatch.setattr(completeness, "measure_duration_seconds",
                        lambda path: per_file)
    monkeypatch.setattr(completeness, "narrator_from_file_tags",
                        lambda paths: list(tag_narrators))
    monkeypatch.setattr(monitor, "_book_for", lambda row: edition)
    row = _row(release_title=release_title)
    return monitor._check_complete(str(tmp_path), row)


def test_unidentified_narration_above_floor_imports(monkeypatch, tmp_path):
    # SeadogsBooty's case: the release names no narrator and the tags name
    # "Martin Shaw", which the tag reader may not treat as a contradiction —
    # so the narration is unidentified, and 886/1164 (0.76) clears the loose
    # plausibility floor.
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=["Martin Shaw"],
        measured_minutes=886,
    )
    assert verdict["complete"] is True
    assert "narrat" in verdict["reason"].lower()


def test_unidentified_narration_below_floor_is_held(monkeypatch, tmp_path):
    # Unidentified is not a free pass: 400/1164 (0.34) is far below any
    # plausible complete reading, so the floor holds it.
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=[],
        measured_minutes=400,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_mistagged_truncated_edition_below_floor_is_held(monkeypatch, tmp_path):
    # Tags are ripper-written and sloppy: they may only confirm the edition's
    # narration, never declare a different one. A truncated Serkis reading
    # mis-tagged "Martin Shaw" reads as unidentified, and 400/1164 is below
    # the floor, so it is still held.
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=["Martin Shaw"],
        measured_minutes=400,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_same_narration_in_tags_is_still_held_when_short(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=["Andy Serkis"],
        measured_minutes=886,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_unknown_narration_above_floor_imports(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=[],
        measured_minutes=886,
    )
    assert verdict["complete"] is True


def test_release_title_naming_edition_narrator_is_still_held(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="The Silmarillion narrated by Andy Serkis",
        tag_narrators=[],
        measured_minutes=886,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_release_title_naming_other_narrator_is_not_held(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="The Silmarillion narrated by Martin Shaw",
        tag_narrators=[],
        measured_minutes=886,
    )
    assert verdict["complete"] is True


def test_different_narration_below_floor_is_held(monkeypatch, tmp_path):
    # A confidently different narration skips the strict gate, but not all
    # checks: 400/1164 is below the plausibility floor, so a fragment or
    # sample naming another narrator is still held.
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="The Silmarillion narrated by Martin Shaw",
        tag_narrators=[],
        measured_minutes=400,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]
    # The hold reason names the alternate narration and the floor, so the
    # Review page doesn't read as a definitive measurement.
    assert "alternate narration: Martin Shaw, below plausibility floor" in \
        verdict["reason"]


def test_abbreviated_tag_narrator_still_matches_edition(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=["A. Serkis"],
        measured_minutes=886,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_no_audio_files_still_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(completeness, "collect_audio", lambda folder: [])
    monkeypatch.setattr(monitor, "_book_for", lambda row: dict(SERKIS_EDITION))
    verdict = monitor._check_complete(str(tmp_path), _row(release_title="x"))
    assert verdict["complete"] is False
    assert "No audio files" in verdict["reason"]


def test_edition_without_narrator_data_keeps_the_hard_gate(monkeypatch, tmp_path):
    # Narrator-awareness needs the edition's narrator to compare against.
    # Without one, the runtime stays the book's only completeness signal.
    edition = dict(SERKIS_EDITION, narrator_names=[])
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=[],
        measured_minutes=886,
        edition=edition,
    )
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_floor_never_tightens_a_lenient_user_tolerance(monkeypatch, tmp_path):
    # A user who set completeness_tolerance to 0.5 must not get downloads
    # held at a 0.55 ratio that the old code imported: the 0.6 floor may
    # only loosen the gate, never tighten it.
    monkeypatch.setattr(completeness, "tolerance_from_settings",
                        lambda: 0.5)
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=[],
        measured_minutes=640,  # 640/1164 = 0.55: above 0.5, below 0.6
    )
    assert verdict["complete"] is True


def test_folder_name_narrator_counts_like_release_title(monkeypatch, tmp_path):    # The download folder's name is checked alongside the release title: a
    # folder naming the edition's narrator is a confident match, so the hard
    # gate holds the short download.
    folder = tmp_path / "The Silmarillion narrated by Andy Serkis"
    folder.mkdir()
    files = []
    for i in range(3):
        path = folder / f"chapter{i + 1:02d}.mp3"
        path.write_bytes(b"\x00" * 64)
        files.append(path)
    per_file = 886 * 60.0 / len(files)
    monkeypatch.setattr(completeness, "collect_audio", lambda f: list(files))
    monkeypatch.setattr(completeness, "measure_duration_seconds",
                        lambda p: per_file)
    monkeypatch.setattr(completeness, "narrator_from_file_tags", lambda ps: [])
    monkeypatch.setattr(monitor, "_book_for", lambda row: dict(SERKIS_EDITION))
    verdict = monitor._check_complete(
        str(folder), _row(release_title="J.R.R. Tolkien - The Silmarillion"))
    assert verdict["complete"] is False
    assert "minutes short" in verdict["reason"]


def test_folder_name_does_not_bleed_into_detected_narrator():
    # The title and folder are checked separately: a folder name must not
    # get captured as part of a narrator name from the title.
    from core.audiobook_completeness import narration_confidence

    result = narration_confidence(
        "/downloads/test_some_junk_folder_name",
        "The Silmarillion narrated by Martin Shaw",
        dict(SERKIS_EDITION),
    )
    assert result["confidence"] == "different"
    assert result["detected_narrator"] == "Martin Shaw"


def test_below_floor_hold_says_narration_unconfirmed(monkeypatch, tmp_path):
    verdict = _check(
        monkeypatch, tmp_path,
        release_title="J.R.R. Tolkien - The Silmarillion",
        tag_narrators=[],
        measured_minutes=400,
    )
    assert verdict["complete"] is False
    assert "(narration unconfirmed)" in verdict["reason"]


# ---------------------------------------------------------------------------
# narrator_from_file_tags: the sensor, tested against real files
# ---------------------------------------------------------------------------

def _synth_mp3(path, tags=None):
    """A minimal file mutagen can actually open: one valid MPEG-1 Layer III
    frame (128kbps/44100Hz) plus optional ID3 tags."""
    from mutagen.id3 import ID3

    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)
    path.write_bytes(frame * 4)
    if tags:
        id3 = ID3()
        for frame_obj in tags:
            id3.add(frame_obj)
        id3.save(str(path))
    return path


def test_tag_reader_finds_tcom(tmp_path):
    from mutagen.id3 import TCOM

    path = _synth_mp3(tmp_path / "a.mp3",
                      [TCOM(encoding=3, text=["Martin Shaw"])])
    assert completeness.narrator_from_file_tags([path]) == ["Martin Shaw"]


def test_tag_reader_finds_txxx_narrator(tmp_path):
    from mutagen.id3 import TXXX

    path = _synth_mp3(tmp_path / "a.mp3",
                      [TXXX(encoding=3, desc="NARRATOR", text=["Andy Serkis"])])
    assert completeness.narrator_from_file_tags([path]) == ["Andy Serkis"]


def test_tag_reader_reads_author_slot_as_narrator_hint(tmp_path):
    # TPE1 is the author slot, but rippers put the narrator there often
    # enough that the reader takes it as a (weak) hint. The value comes back
    # as an ordinary candidate name; whether it may only confirm (never
    # contradict) is decided by narration_confidence, not here.
    from mutagen.id3 import TPE1

    path = _synth_mp3(tmp_path / "a.mp3",
                      [TPE1(encoding=3, text=["J. R. R. Tolkien"])])
    assert completeness.narrator_from_file_tags([path]) == ["J. R. R. Tolkien"]


def test_tag_reader_splits_multi_value_id3_frame(tmp_path):
    # ID3v2.4 multi-value frames serialize with embedded null bytes; the
    # reader must return clean separate names, not one leaked blob.
    from mutagen.id3 import TCOM

    path = _synth_mp3(tmp_path / "a.mp3",
                      [TCOM(encoding=3, text=["Michael Kramer", "Kate Reading"])])
    assert completeness.narrator_from_file_tags([path]) == [
        "Michael Kramer", "Kate Reading"]


def test_tag_reader_returns_empty_for_untagged_files(tmp_path):
    path = _synth_mp3(tmp_path / "a.mp3")
    assert completeness.narrator_from_file_tags([path]) == []


def test_tag_reader_skips_corrupt_files(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"this is not audio at all")
    assert completeness.narrator_from_file_tags([bad]) == []


def test_tag_reader_collects_distinct_names_across_files(tmp_path):
    from mutagen.id3 import TCOM

    first = _synth_mp3(tmp_path / "a.mp3",
                       [TCOM(encoding=3, text=["Martin Shaw"])])
    second = _synth_mp3(tmp_path / "b.mp3",
                        [TCOM(encoding=3, text=["Andy Serkis"])])
    assert completeness.narrator_from_file_tags([first, second]) == [
        "Martin Shaw", "Andy Serkis"]


def test_tag_reader_samples_at_most_five_files(tmp_path, monkeypatch):
    from mutagen.id3 import TCOM

    import mutagen

    calls = []
    original = mutagen.File

    def counting(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    # `from mutagen import File` happens inside the reader, so patching the
    # attribute takes effect on the next call.
    files = [_synth_mp3(tmp_path / f"c{i:02d}.mp3",
                        [TCOM(encoding=3, text=[f"Narrator {i}"])])
             for i in range(8)]
    monkeypatch.setattr(mutagen, "File", counting)
    names = completeness.narrator_from_file_tags(files)
    assert len(calls) == 5
    assert names == [f"Narrator {i}" for i in range(5)]


def test_tag_reader_checks_mp4_and_vorbis_narrator_names(tmp_path, monkeypatch):
    # These pin the lookup table for containers that are impractical to
    # synthesize here, using stand-in tag mappings shaped like the real ones
    # (MP4/vorbis tags are plain dicts of lists).
    import mutagen

    cases = [
        ({"\xa9wrt": ["Martin Shaw"]}, ["Martin Shaw"]),   # MP4/m4b
        ({"NARRATOR": ["Martin Shaw"]}, ["Martin Shaw"]),  # vorbis comments
        ({"COMPOSER": ["Martin Shaw"]}, ["Martin Shaw"]),
    ]
    for tags, expected in cases:
        class FakeAudio:
            pass
        fake = FakeAudio()
        fake.tags = tags
        monkeypatch.setattr(mutagen, "File", lambda *a, **k: fake)
        path = tmp_path / "x.mp3"
        path.write_bytes(b"\x00")
        assert completeness.narrator_from_file_tags([path]) == expected
