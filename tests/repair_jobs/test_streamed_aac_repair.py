"""Skowl: AAC Tidal sent in streamed pieces, saved as it came, reads no
length (0:00, can't seek). New downloads are fixed on the way in; this is the
Tools job for the files already in the library, without re-downloading and
without losing a tag."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

import core.downloads.mp4_remux as remux

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


def _streamed_aac(path):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=12",
                    "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-frag_duration", "2000000", str(path)], check=True)


@needs_ffmpeg
def test_a_library_file_gets_its_length_and_keeps_every_tag(tmp_path):
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    path = tmp_path / "track.m4a"
    _streamed_aac(path)
    tagged = MP4(str(path))
    if tagged.tags is None:
        tagged.add_tags()
    tagged.tags["\xa9nam"] = ["Without You"]
    tagged.tags["\xa9ART"] = ["Cosmic Quest"]
    tagged.tags["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0fakejpeg", imageformat=MP4Cover.FORMAT_JPEG)]
    tagged.tags["----:com.apple.iTunes:MusicBrainz Track Id"] = [MP4FreeForm(b"mb-123")]
    tagged.save()
    before = dict(MP4(str(path)).tags)
    assert remux.reads_zero_length(path)

    assert remux.repair_streamed_mp4(path, FFMPEG) is True
    fixed = MP4(str(path))
    assert fixed.info.length == pytest.approx(12, abs=0.2)
    assert dict(fixed.tags) == before
    assert not remux.reads_zero_length(path)
    assert not (tmp_path / "track.fix.m4a").exists()


def test_a_failed_fix_leaves_the_file_exactly_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "track.m4a"
    path.write_bytes(b"original bytes")

    class _Tags(dict):
        pass

    monkeypatch.setattr("mutagen.mp4.MP4", lambda p: SimpleNamespace(tags=_Tags(), info=SimpleNamespace(length=0)))

    def boom(src, dst, ffmpeg):
        dst.write_bytes(b"half written")
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(remux, "_remux_into", boom)
    assert remux.repair_streamed_mp4(path, "ffmpeg") is False
    assert path.read_bytes() == b"original bytes"
    assert not (tmp_path / "track.fix.m4a").exists()


def test_the_scan_flags_only_aac_that_reads_no_length(tmp_path, monkeypatch):
    import core.library2.maintenance_subjects as subjects
    import core.repair_jobs.filesystem_subjects as fs
    import core.repair_jobs.streamed_aac_repair as job_mod

    files = {name: tmp_path / name for name in ("bad.m4a", "good.m4a", "song.flac")}
    for p in files.values():
        p.write_bytes(b"x")
    monkeypatch.setattr(job_mod, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(subjects, "active_file_subjects", lambda db, cfg: [
        {"path": str(p), "track_id": i, "title": name, "artist_name": "A"}
        for i, (name, p) in enumerate(files.items(), 1)])
    monkeypatch.setattr(subjects, "subject_details", lambda s: {})
    monkeypatch.setattr(fs, "filesystem_audio_files", lambda ctx: [])
    monkeypatch.setattr(remux, "reads_zero_length", lambda p: str(p).endswith("bad.m4a"))
    found = []
    ctx = SimpleNamespace(
        db=None, config_manager=None, transfer_folder=str(tmp_path), scope=None,
        update_progress=None, report_progress=None, check_stop=lambda: False,
        wait_if_paused=lambda: False,
        create_finding=lambda **kw: found.append(kw) or True)
    result = job_mod.StreamedAacRepairJob().scan(ctx)
    assert [f["finding_type"] for f in found] == ["zero_length_mp4"]
    assert found[0]["file_path"].endswith("bad.m4a")
    assert result.scanned == 2   # the flac is never opened


def test_no_ffmpeg_no_findings(monkeypatch):
    import core.repair_jobs.streamed_aac_repair as job_mod
    monkeypatch.setattr(job_mod, "ffmpeg_path", lambda: None)
    ctx = SimpleNamespace(create_finding=lambda **kw: pytest.fail("no finding without ffmpeg"))
    assert job_mod.StreamedAacRepairJob().scan(ctx).findings_created == 0


def test_the_fix_runs_through_the_worker(tmp_path, monkeypatch):
    import core.repair_jobs.streamed_aac_repair as job_mod
    from core.repair_worker import RepairWorker
    path = tmp_path / "bad.m4a"
    path.write_bytes(b"x")
    worker = RepairWorker.__new__(RepairWorker)
    worker._resolve_finding_path = lambda entity_id, raw: (raw, None)
    monkeypatch.setattr(job_mod, "ffmpeg_path", lambda: "ffmpeg")
    calls = []
    monkeypatch.setattr(remux, "reads_zero_length", lambda p: True)
    monkeypatch.setattr(remux, "repair_streamed_mp4", lambda p, ff: calls.append(p) or True)
    out = worker._fix_zero_length_mp4('file', None, str(path), {'file_path': str(path)})
    assert out['success'] is True and out['action'] == 'fixed_length'
    assert calls == [str(path)]
    gone = worker._fix_zero_length_mp4('file', None, str(tmp_path / "x.m4a"), {})
    assert gone['success'] is False and gone.get('stale') is True
