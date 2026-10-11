"""Tests for core/audiobook_m4b_merge.py.

Everything runs against tmp_path. The ffmpeg/ffprobe-dependent paths are
mocked except for one real end-to-end test, which skips when no ffmpeg is on
PATH. None of these tests can reach a configured library.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core import audiobook_m4b_merge as m4b_merge
from core.audiobook_m4b_merge import (
    build_concat_list,
    build_ffmetadata,
    chapter_title,
    merge_book_files,
    sweep_unverifiable_m4b,
)

BOOK = {"asin": "B08G9PRS1K", "title": "The Final Empire"}


class _Manager:
    """config_manager stand-in: only .get matters here."""

    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _enabled():
    return patch("core.settings.config_manager",
                 _Manager({"audiobooks.merge_m4b": True}))


def _two_files(tmp_path):
    first = tmp_path / "01 - The Beginning.mp3"
    second = tmp_path / "02 - The Middle.mp3"
    first.write_bytes(b"fake-audio-one")
    second.write_bytes(b"fake-audio-two")
    return [str(first), str(second)]


def _fake_run_factory(durations=None, codec="mp3", make_output=True,
                      sample_rate="44100", channels="2", profile="LC",
                      chapter_count=2):
    """A subprocess.run stand-in. ffprobe answers from `durations` (anything
    unlisted reports 10.0s); the stream probe reports the given codec
    parameters; the chapter probe reports `chapter_count` chapters; the ffmpeg
    call writes `make_output` bytes to its last argument. Every command seen
    is recorded on `fake_run.seen`."""
    durations = durations or {}

    def fake_run(cmd, **kwargs):
        fake_run.seen.append(list(cmd))
        binary = Path(cmd[0]).name
        if binary == "ffprobe":
            target = Path(cmd[-1]).name
            if any("stream=codec_name" in a for a in cmd):
                import json as _json
                stdout = _json.dumps({
                    "streams": [{
                        "codec_name": codec,
                        "sample_rate": sample_rate,
                        "channels": int(channels),
                        "profile": profile,
                    }],
                    "format": {"duration": durations.get(target, "10.0")},
                })
            elif any("chapter" in a for a in cmd):
                # chapter=id: one integer per line, like real ffprobe.
                stdout = "".join(f"{i}\n" for i in range(chapter_count))
            else:
                raise AssertionError(f"unexpected ffprobe call in test: {cmd}")
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if binary == "ffmpeg":
            if make_output:
                Path(cmd[-1]).write_bytes(b"fake-m4b-bytes")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected binary in test: {binary}")

    fake_run.seen = []
    return fake_run


def _resolved(monkeypatch):
    """Pin ffmpeg/ffprobe resolution so tests do not depend on PATH."""
    monkeypatch.setattr(m4b_merge, "_resolve_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(m4b_merge, "_resolve_ffprobe", lambda _: "/usr/bin/ffprobe")


# ---------------------------------------------------------------------------
# Passthrough paths
# ---------------------------------------------------------------------------

def test_disabled_setting_returns_originals(tmp_path):
    # No config patch: the real default is False, so the switch is off.
    files = _two_files(tmp_path)
    assert merge_book_files(tmp_path, files, BOOK) == files
    assert all(Path(f).exists() for f in files)


def test_single_file_is_left_alone(tmp_path):
    only = tmp_path / "The Final Empire.m4b"
    only.write_bytes(b"already-merged")
    with _enabled():
        assert merge_book_files(tmp_path, [str(only)], BOOK) == [str(only)]
    assert only.exists()


def test_missing_ffmpeg_leaves_the_book_alone(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    monkeypatch.setattr(m4b_merge.shutil, "which", lambda _name: None)
    with _enabled():
        assert merge_book_files(tmp_path, files, BOOK) == files
    assert all(Path(f).exists() for f in files)


def test_unprobable_file_aborts_the_merge(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    _resolved(monkeypatch)

    def bad_probe(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(m4b_merge.subprocess, "run", bad_probe)
    with _enabled():
        assert merge_book_files(tmp_path, files, BOOK) == files
    assert all(Path(f).exists() for f in files)


# ---------------------------------------------------------------------------
# Chapter titles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,index,expected", [
    ("01 - The Beginning.mp3", 1, "The Beginning"),
    ("001 The End.mp3", 2, "The End"),
    ("07_Prologue.mp3", 3, "Prologue"),
    ("12.Epilogue.mp3", 4, "Epilogue"),
    ("chapter5.mp3", 5, "chapter5"),
    ("001.mp3", 6, "Chapter 6"),
    ("---.mp3", 7, "Chapter 7"),
])
def test_chapter_title_cleaning(name, index, expected):
    assert chapter_title(Path(name), index) == expected


# ---------------------------------------------------------------------------
# ffmetadata and concat list rendering
# ---------------------------------------------------------------------------

def test_build_ffmetadata_exact_format():
    chapters = [(0, 60000, "The Beginning"), (60000, 125000, "A; tricky = title #1")]
    assert build_ffmetadata(chapters) == (
        ";FFMETADATA1\n"
        "[CHAPTER]\n"
        "TIMEBASE=1/1000\n"
        "START=0\n"
        "END=60000\n"
        "title=The Beginning\n"
        "[CHAPTER]\n"
        "TIMEBASE=1/1000\n"
        "START=60000\n"
        "END=125000\n"
        "title=A\\; tricky \\= title \\#1\n"
    )


def test_build_concat_list_escapes_single_quotes():
    files = [Path("/lib/it's/01 - a.mp3"), Path("/lib/b.mp3")]
    assert build_concat_list(files) == "file '/lib/it'\\''s/01 - a.mp3'\nfile '/lib/b.mp3'\n"


# ---------------------------------------------------------------------------
# Mocked end-to-end paths
# ---------------------------------------------------------------------------

def test_success_reencodes_deletes_sources_and_returns_the_merge(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    _resolved(monkeypatch)
    # Inputs probe at 10s each; the merged file must report their sum.
    fake_run = _fake_run_factory(durations={".The Final Empire.m4b.partial": "20.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert len(result) == 1
    merged = Path(result[0])
    assert merged.suffix == ".m4b" and merged.is_file()
    assert merged.name.startswith("The Final Empire")
    # The sources are gone; nothing else in the folder was touched.
    assert not Path(files[0]).exists() and not Path(files[1]).exists()

    ffmpeg_cmds = [cmd for cmd in fake_run.seen if Path(cmd[0]).name == "ffmpeg"]
    assert len(ffmpeg_cmds) == 1
    cmd = ffmpeg_cmds[0]
    # mp3 inputs are not mp4-family AAC, so this must re-encode, not copy.
    assert "-c:a" in cmd and "aac" in cmd
    assert "-map_metadata" in cmd and "1" in cmd


def test_all_aac_mp4_inputs_are_stream_copied(tmp_path, monkeypatch):
    first = tmp_path / "01 - A.m4b"
    second = tmp_path / "02 - B.m4b"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    files = [str(first), str(second)]
    _resolved(monkeypatch)
    fake_run = _fake_run_factory(codec="aac",
                                 durations={".The Final Empire.m4b.partial": "20.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert len(result) == 1 and Path(result[0]).is_file()
    ffmpeg_cmds = [cmd for cmd in fake_run.seen if Path(cmd[0]).name == "ffmpeg"]
    assert "-c" in ffmpeg_cmds[0] and "copy" in ffmpeg_cmds[0]
    assert "-c:a" not in ffmpeg_cmds[0]


def test_verify_failure_deletes_the_partial_and_keeps_originals(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    _resolved(monkeypatch)
    # The inputs probe at 10s each but the "merged" file claims 9999s, so
    # verification must fail: the partial goes, the originals stay.
    fake_run = _fake_run_factory(durations={".The Final Empire.m4b.partial": "9999.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert result == files
    assert all(Path(f).exists() for f in files)
    assert not (tmp_path / "The Final Empire.m4b").exists()


def test_failed_ffmpeg_keeps_everything(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    _resolved(monkeypatch)

    def failing_run(cmd, **kwargs):
        import json as _json
        binary = Path(cmd[0]).name
        if binary == "ffprobe":
            target = Path(cmd[-1]).name
            if "stream=codec_name" in " ".join(cmd):
                stdout = _json.dumps({
                    "streams": [{"codec_name": "mp3", "sample_rate": "44100",
                                 "channels": 2, "profile": "unknown"}],
                    "format": {"duration": "10.0"},
                })
            else:
                stdout = "0\n1\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="encode boom")

    monkeypatch.setattr(m4b_merge.subprocess, "run", failing_run)
    with _enabled():
        assert merge_book_files(tmp_path, files, BOOK) == files
    assert all(Path(f).exists() for f in files)
    assert not (tmp_path / "The Final Empire.m4b").exists()


def test_sidecars_are_never_deleted(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"fake-cover")
    _resolved(monkeypatch)
    fake_run = _fake_run_factory(durations={".The Final Empire.m4b.partial": "20.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert len(result) == 1
    assert cover.exists()


# ---------------------------------------------------------------------------
# Real end-to-end (needs ffmpeg on PATH)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_real_merge_of_two_wavs(tmp_path):
    first = tmp_path / "01 - Opening.wav"
    second = tmp_path / "02 - Closing.wav"
    for path, freq in ((first, 440), (second, 660)):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"sine=frequency={freq}:duration=2",
             "-c:a", "pcm_s16le", str(path)],
            check=True, capture_output=True,
        )
    with _enabled():
        result = merge_book_files(tmp_path, [str(first), str(second)], BOOK)

    assert len(result) == 1
    merged = Path(result[0])
    assert merged.suffix == ".m4b" and merged.is_file()
    assert not first.exists() and not second.exists()

    chapters = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "chapter=id",
         "-of", "csv=p=0", str(merged)],
        check=True, capture_output=True, text=True,
    )
    assert [line for line in chapters.stdout.splitlines() if line.strip()] == ["0", "1"]

    titles = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "chapter_tags=title",
         "-of", "default=nw=1:nk=1", str(merged)],
        check=True, capture_output=True, text=True,
    )
    assert "Opening" in titles.stdout
    assert "Closing" in titles.stdout


# ---------------------------------------------------------------------------
# Already-merged / retry guards
# ---------------------------------------------------------------------------

def test_merge_skipped_when_m4b_already_filed(tmp_path, monkeypatch):
    """A retry after a crash past the verify step (or a hand-placed .m4b)
    must never stack a second merge on top of the finished book."""
    files = _two_files(tmp_path)
    existing = tmp_path / "The Final Empire.m4b"
    existing.write_bytes(b"already-here")
    _resolved(monkeypatch)
    fake_run = _fake_run_factory()
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        assert merge_book_files(tmp_path, files, BOOK) == files
    assert existing.read_bytes() == b"already-here"
    assert not (tmp_path / "The Final Empire (2).m4b").exists()
    assert all(Path(f).exists() for f in files)
    ffmpeg_cmds = [c for c in fake_run.seen if Path(c[0]).name == "ffmpeg"]
    assert ffmpeg_cmds == []


def _organize_row():
    import json
    return {
        "download_id": "d9",
        "asin": "B08G9PRS1K",
        "title": "The Final Empire",
        "author": "Brandon Sanderson",
        "release_title": "Some Release",
        "book_json": json.dumps({
            "asin": "B08G9PRS1K",
            "title": "The Final Empire",
            "author_names": ["Brandon Sanderson"],
        }),
    }


def test_organize_skips_merge_when_some_files_were_already_filed(tmp_path):
    """A retry that only wrote the remainder must not merge a subset: the
    merge would fold a partial book and strand the rest as loose files."""
    import core.audiobook_download_monitor as monitor

    written = str(tmp_path / "02 - B.mp3")
    Path(written).write_bytes(b"x")
    already = str(tmp_path / "01 - A.mp3")

    def fake_organize(source_dir, book, library_root, **kwargs):
        return {"ok": True, "path": str(tmp_path), "files": [written],
                "skipped": [already], "error": ""}

    merge_calls = []
    tagged = {}
    with patch("core.audiobook_organizer.organize_download", fake_organize), \
         patch("core.audiobook_m4b_merge.merge_book_files",
               lambda folder, files, book: merge_calls.append(files) or ["nope"]), \
         patch("core.audiobook_post_processor.post_process_book",
               lambda folder, book, files: tagged.update(files=files)):
        result = monitor._organize("/downloads/book", _organize_row())

    assert result["ok"] is True
    assert merge_calls == []
    assert tagged["files"] == [written]


def test_organize_tags_the_merged_file_list(tmp_path):
    """The happy path: merge runs on a clean import and the post-processor
    tags the single .m4b, not the deleted chapter files."""
    import core.audiobook_download_monitor as monitor

    written = [str(tmp_path / "01 - A.mp3"), str(tmp_path / "02 - B.mp3")]
    for name in written:
        Path(name).write_bytes(b"x")
    merged = [str(tmp_path / "The Final Empire.m4b")]

    def fake_organize(source_dir, book, library_root, **kwargs):
        return {"ok": True, "path": str(tmp_path), "files": written,
                "skipped": [], "error": ""}

    seen = {}
    with patch("core.audiobook_organizer.organize_download", fake_organize), \
         patch("core.audiobook_m4b_merge.merge_book_files",
               lambda folder, files, book: seen.update(files=files) or merged), \
         patch("core.audiobook_post_processor.post_process_book",
               lambda folder, book, files: seen.update(tagged=files)):
        monitor._organize("/downloads/book", _organize_row())

    assert seen["files"] == written
    assert seen["tagged"] == merged


# ---------------------------------------------------------------------------
# Stream-copy uniformity (review F1) — real ffmpeg
# ---------------------------------------------------------------------------

def _spy_run(monkeypatch):
    """subprocess.run that records commands but really executes them."""
    real = subprocess.run
    seen = []

    def spy(cmd, **kwargs):
        seen.append(list(cmd))
        return real(cmd, **kwargs)

    monkeypatch.setattr(m4b_merge.subprocess, "run", spy)
    return seen


def _make_aac(path, freq, sample_rate):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"sine=frequency={freq}:duration=2",
         "-ar", str(sample_rate), "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )


def _real_chapter_count(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "chapter=id",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return sum(1 for line in out.stdout.splitlines() if line.strip())


def _real_chapter_titles(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "chapter_tags=title",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return out.stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_real_copy_path_with_uniform_aac(tmp_path, monkeypatch):
    """Two AAC files with identical parameters take the stream-copy path and
    keep their chapters — the riskiest branch, exercised for real."""
    first = tmp_path / "01 - Alpha.m4a"
    second = tmp_path / "02 - Beta.m4a"
    _make_aac(first, 440, 44100)
    _make_aac(second, 660, 44100)
    seen = _spy_run(monkeypatch)
    with _enabled():
        result = merge_book_files(tmp_path, [str(first), str(second)], BOOK)

    assert len(result) == 1 and Path(result[0]).suffix == ".m4b"
    assert not first.exists() and not second.exists()
    cmds = [c for c in seen if Path(c[0]).name == "ffmpeg"]
    assert len(cmds) == 1
    assert "-c" in cmds[0] and "copy" in cmds[0]
    assert "-c:a" not in cmds[0]
    assert "-map" in cmds[0] and "0:a:0" in cmds[0]
    assert _real_chapter_count(result[0]) == 2
    titles = _real_chapter_titles(result[0])
    assert "Alpha" in titles and "Beta" in titles


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_real_mismatched_sample_rates_fall_back_to_reencode(tmp_path, monkeypatch):
    """Mixed AAC parameters must NOT stream-copy: the concat demuxer would
    otherwise emit later chapters at the wrong speed."""
    first = tmp_path / "01 - Alpha.m4a"
    second = tmp_path / "02 - Beta.m4a"
    _make_aac(first, 440, 44100)
    _make_aac(second, 660, 48000)
    seen = _spy_run(monkeypatch)
    with _enabled():
        result = merge_book_files(tmp_path, [str(first), str(second)], BOOK)

    assert len(result) == 1 and Path(result[0]).suffix == ".m4b"
    assert not first.exists() and not second.exists()
    cmds = [c for c in seen if Path(c[0]).name == "ffmpeg"]
    assert "-c:a" in cmds[0] and "aac" in cmds[0]
    assert _real_chapter_count(result[0]) == 2


# ---------------------------------------------------------------------------
# Failed-encode cleanup (review F2) and stray-partial sweep (review F3)
# ---------------------------------------------------------------------------

def test_failed_encode_removes_the_partial(tmp_path, monkeypatch):
    """An ffmpeg failure must not strand a corrupt .m4b in the library —
    the next tick would see it and could never recover the name."""
    files = _two_files(tmp_path)
    _resolved(monkeypatch)
    fake = _fake_run_factory()

    def failing(cmd, **kwargs):
        result = fake(cmd, **kwargs)
        if Path(cmd[0]).name == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return result

    monkeypatch.setattr(m4b_merge.subprocess, "run", failing)
    with _enabled():
        assert merge_book_files(tmp_path, files, BOOK) == files
    assert all(Path(f).exists() for f in files)
    assert list(tmp_path.glob("*.m4b")) == []
    assert list(tmp_path.glob(".*.m4b.partial")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_sweep_renames_unplayable_stray_m4b(tmp_path):
    """A crashed encode's partial has no usable moov atom: the sweep moves it
    aside (never deletes) so it is neither ingested nor squatting the name."""
    stray = tmp_path / "Title.m4b"
    stray.write_bytes(b"this is not audio")
    chapter = tmp_path / "01 - A.mp3"
    chapter.write_bytes(b"x")
    renamed = sweep_unverifiable_m4b(tmp_path, [str(chapter)])
    assert renamed == [str(tmp_path / "Title.m4b.unverified-partial")]
    assert not stray.exists()
    assert chapter.exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
def test_sweep_keeps_playable_m4b(tmp_path):
    """A real, playable .m4b that is not a known input is strictly left
    alone — it might be the listener's own file."""
    good = tmp_path / "Title.m4b"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "aac", str(good)],
        check=True, capture_output=True,
    )
    assert sweep_unverifiable_m4b(tmp_path, []) == []
    assert good.exists()


def test_sweep_fails_open_without_ffmpeg(tmp_path):
    stray = tmp_path / "Title.m4b"
    stray.write_bytes(b"garbage")
    with patch.object(m4b_merge, "_resolve_ffmpeg", return_value=None):
        assert sweep_unverifiable_m4b(tmp_path, []) == []
    assert stray.exists()


def test_organize_sweeps_stray_partial_on_retry(tmp_path):
    """The retry path never enters the merge, so the wire-in sweeps a
    crashed partial aside itself (merge still not called)."""
    import core.audiobook_download_monitor as monitor

    written = str(tmp_path / "02 - B.mp3")
    Path(written).write_bytes(b"x")
    already = str(tmp_path / "01 - A.mp3")
    stray = tmp_path / "Title.m4b"
    stray.write_bytes(b"garbage")

    def fake_organize(source_dir, book, library_root, **kwargs):
        return {"ok": True, "path": str(tmp_path), "files": [written],
                "skipped": [already], "error": ""}

    merge_calls, sweep_calls, tagged = [], [], {}
    with _enabled(), \
         patch("core.audiobook_organizer.organize_download", fake_organize), \
         patch("core.audiobook_m4b_merge.merge_book_files",
               lambda folder, files, book: merge_calls.append(files) or ["nope"]), \
         patch("core.audiobook_m4b_merge.sweep_unverifiable_m4b",
               lambda folder, known: sweep_calls.append((folder, list(known))) or []), \
         patch("core.audiobook_post_processor.post_process_book",
               lambda folder, book, files: tagged.update(files=files)):
        monitor._organize("/downloads/book", _organize_row())

    assert merge_calls == []
    assert len(sweep_calls) == 1
    assert sweep_calls[0][0] == str(tmp_path)
    assert set(sweep_calls[0][1]) == {written, already}
    assert tagged["files"] == [written]


def test_stale_partial_temp_is_cleaned_at_merge_start(tmp_path, monkeypatch):
    """A hard-killed encode leaves .<Title>.m4b.partial behind; the next merge
    removes it before starting — it is ours, hidden, and clearly marked."""
    files = _two_files(tmp_path)
    stale = tmp_path / ".The Final Empire.m4b.partial"
    stale.write_bytes(b"crash leftover")
    _resolved(monkeypatch)
    fake_run = _fake_run_factory(
        durations={".The Final Empire.m4b.partial": "20.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert len(result) == 1 and Path(result[0]).suffix == ".m4b"
    assert not stale.exists()
    assert list(tmp_path.glob(".*.m4b.partial")) == []
    # The encode really went to the temp file, then renamed into place.
    ffmpeg_cmds = [c for c in fake_run.seen if Path(c[0]).name == "ffmpeg"]
    assert ffmpeg_cmds[0][-1].endswith(".m4b.partial")
    assert Path(result[0]).is_file()


def test_successful_merge_leaves_no_partial_temp(tmp_path, monkeypatch):
    files = _two_files(tmp_path)
    _resolved(monkeypatch)
    fake_run = _fake_run_factory(
        durations={".The Final Empire.m4b.partial": "20.0"})
    monkeypatch.setattr(m4b_merge.subprocess, "run", fake_run)
    with _enabled():
        result = merge_book_files(tmp_path, files, BOOK)

    assert len(result) == 1
    assert list(tmp_path.glob(".*.m4b.partial")) == []
    assert list(tmp_path.glob("*.m4b")) == [Path(result[0])]
