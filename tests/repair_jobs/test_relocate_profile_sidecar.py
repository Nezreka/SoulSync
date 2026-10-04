"""#1504 — AcoustID relocate carries the owner across the staging hop.

``relocate_mismatch_to_staging`` drops the tracks row, which used to kill
``owner_profile_id``; the staged file then re-imported into the shared
folder. Now the relocate call site writes a ``.soulsync-profile.json``
sidecar next to the staged file, the auto-import worker consumes it into
``context['profile_id']`` (so the file routes back to the own folder),
and the sidecar is deleted after a successful import.

Hermetic: real tmp files; the pipeline callback is faked.
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

# ── test-env shim ──────────────────────────────────────────────────────────
# mutagen is not installed in this environment (known env gap — several
# existing test modules are excluded for the same reason). auto_import_worker
# pulls it in transitively via core.imports.pipeline → core.tag_writer.
# Stub the pieces tag_writer imports at module level; the tag READS under
# test here go through _read_file_tags, which degrades gracefully when the
# stub can't open a file.
def _stub_mutagen():
    if "mutagen" in sys.modules:
        return

    class _Cls:
        def __init__(self, *a, **k):
            pass

    class _APENoHeaderError(Exception):
        pass

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m

    _mod("mutagen", File=_Cls)
    _mod("mutagen.id3", ID3=_Cls, TIT2=_Cls, TPE1=_Cls, TALB=_Cls,
         TDRC=_Cls, TRCK=_Cls, TCON=_Cls, TPE2=_Cls, TPOS=_Cls,
         TXXX=_Cls, APIC=_Cls, TBPM=_Cls)
    _mod("mutagen.flac", FLAC=_Cls, Picture=_Cls)
    _mod("mutagen.mp4", MP4=_Cls, MP4Cover=_Cls, MP4FreeForm=_Cls)
    _mod("mutagen.oggvorbis", OggVorbis=_Cls)
    _mod("mutagen.apev2", APEv2=_Cls, APENoHeaderError=_APENoHeaderError)


_stub_mutagen()

from core.auto_import_worker import (
    AUDIO_EXTENSIONS,
    AutoImportWorker,
    FolderCandidate,
    _delete_profile_sidecar,
    _read_profile_sidecar,
)

# The stub above must not leak: pytest imports every test module at
# collection time, and a lingering fake ``mutagen`` in sys.modules would
# change what the tag-writing test modules see (they expect the real
# ModuleNotFoundError on this env). The already-imported modules keep
# their bound references; only the registry entries are dropped.
for _name in [n for n in sys.modules if n == "mutagen" or n.startswith("mutagen.")]:
    del sys.modules[_name]
del _name
from core.repair_jobs.relocate import (
    PROFILE_SIDECAR_SUFFIX,
    profile_sidecar_path,
)


# ── sidecar naming ───────────────────────────────────────────────────────

def test_sidecar_path_appends_to_full_filename():
    assert profile_sidecar_path("/stg/03 - x.mp3") == \
        "/stg/03 - x.mp3" + PROFILE_SIDECAR_SUFFIX


def test_sidecar_never_importable_media():
    # the staging scan only picks up AUDIO_EXTENSIONS — a .json sidecar can
    # never become an import candidate itself
    assert os.path.splitext("x.mp3" + PROFILE_SIDECAR_SUFFIX)[1].lower() \
        not in AUDIO_EXTENSIONS


# ── read / delete helpers ────────────────────────────────────────────────

def test_sidecar_round_trip(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    sidecar = tmp_path / ("track.flac" + PROFILE_SIDECAR_SUFFIX)
    sidecar.write_text(json.dumps({"profile_id": 2}), encoding="utf-8")

    assert _read_profile_sidecar(str(staged)) == 2

    _delete_profile_sidecar(str(staged))
    assert not sidecar.exists()
    assert staged.exists()  # the audio file itself is untouched


def test_sidecar_missing_is_none(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    assert _read_profile_sidecar(str(staged)) is None
    _delete_profile_sidecar(str(staged))  # no-op, never raises


def test_sidecar_broken_json_is_none(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    (tmp_path / ("track.flac" + PROFILE_SIDECAR_SUFFIX)).write_text(
        "{not json", encoding="utf-8")
    assert _read_profile_sidecar(str(staged)) is None


def test_sidecar_zero_profile_is_none(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    (tmp_path / ("track.flac" + PROFILE_SIDECAR_SUFFIX)).write_text(
        json.dumps({"profile_id": 0}), encoding="utf-8")
    assert _read_profile_sidecar(str(staged)) is None


# ── fingerprint: a stale sidecar must never mis-route ─────────────────────

def _write_fingerprinted_sidecar(staged):
    from core.repair_jobs.relocate import write_profile_sidecar
    assert write_profile_sidecar(str(staged), 2, str(staged))


def test_sidecar_fingerprint_match_is_trusted(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    _write_fingerprinted_sidecar(staged)
    assert _read_profile_sidecar(str(staged)) == 2


def test_sidecar_stale_size_is_ignored(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    _write_fingerprinted_sidecar(staged)
    # the staged file was replaced by a DIFFERENT file under the same name
    staged.write_bytes(b"\x00" * 64)
    assert _read_profile_sidecar(str(staged)) is None


def test_sidecar_stale_mtime_is_ignored(tmp_path):
    staged = tmp_path / "track.flac"
    staged.write_bytes(b"\x00" * 32)
    _write_fingerprinted_sidecar(staged)
    st = os.stat(str(staged))
    os.utime(str(staged), (st.st_atime, st.st_mtime + 30))
    assert _read_profile_sidecar(str(staged)) is None


def test_sidecar_write_failure_is_fail_open(tmp_path):
    # a sidecar that can't be written must not raise — the file just
    # re-imports into the shared folder (today's behavior)
    from core.repair_jobs.relocate import write_profile_sidecar
    assert write_profile_sidecar(
        "/nonexistent-dir-xyz/track.flac", 2, "/nonexistent-dir-xyz/track.flac") is False


# ── _process_matches consumes the sidecar into context['profile_id'] ────

class _Config:
    def get(self, key, default=None):
        return default


def _make_worker(staging, callback):
    return AutoImportWorker(
        database=None,
        staging_path=str(staging),
        process_callback=callback,
        config_manager=_Config(),
    )


def _candidate(staging, audio_file):
    return FolderCandidate(
        path=str(staging), name="single", audio_files=[audio_file],
        is_single=True, is_staging_root=True,
    )


def _match_result(audio_file):
    return {
        "matches": [{
            "track": {"name": "Song", "track_number": 1, "disc_number": 1,
                      "id": "t1", "duration_ms": 1000,
                      "artists": [{"name": "Artist"}]},
            "file": audio_file,
            "confidence": 0.95,
        }],
        "album_data": {},
        "confidence": 0.95,
        "total_tracks": 1,
        "matched_count": 1,
    }


def _identification():
    return {
        "source": "deezer", "artist_name": "Artist", "album_name": "Album",
        "image_url": "", "release_date": "", "artist_id": "a1", "album_id": "b1",
    }


def test_process_matches_threads_sidecar_profile_into_context(tmp_path):
    staging = tmp_path / "Staging"
    staging.mkdir()
    audio = staging / "track.flac"
    audio.write_bytes(b"\x00" * 64)
    (staging / ("track.flac" + PROFILE_SIDECAR_SUFFIX)).write_text(
        json.dumps({"profile_id": 2}), encoding="utf-8")

    seen = {}

    def fake_callback(context_key, context, file_path):
        seen.update(context)
        # the pipeline would move the file and flag success; emulate that
        context["_pipeline_import_succeeded"] = True
        context["_final_path"] = file_path

    worker = _make_worker(staging, fake_callback)
    status, errors = worker._process_matches(
        _candidate(staging, str(audio)), _identification(),
        _match_result(str(audio)))

    assert status == "completed", errors
    assert seen.get("profile_id") == 2
    # sidecar cleaned up after the successful import
    assert not (staging / ("track.flac" + PROFILE_SIDECAR_SUFFIX)).exists()


def test_process_matches_no_sidecar_no_profile_id(tmp_path):
    staging = tmp_path / "Staging"
    staging.mkdir()
    audio = staging / "track.flac"
    audio.write_bytes(b"\x00" * 64)

    seen = {}

    def fake_callback(context_key, context, file_path):
        seen.update(context)
        context["_pipeline_import_succeeded"] = True
        context["_final_path"] = file_path

    worker = _make_worker(staging, fake_callback)
    status, errors = worker._process_matches(
        _candidate(staging, str(audio)), _identification(),
        _match_result(str(audio)))

    assert status == "completed", errors
    assert "profile_id" not in seen  # shared import, exactly as before


def test_failed_import_keeps_sidecar_for_retry(tmp_path):
    staging = tmp_path / "Staging"
    staging.mkdir()
    audio = staging / "track.flac"
    audio.write_bytes(b"\x00" * 64)
    sidecar = staging / ("track.flac" + PROFILE_SIDECAR_SUFFIX)
    sidecar.write_text(json.dumps({"profile_id": 2}), encoding="utf-8")

    def failing_callback(context_key, context, file_path):
        raise RuntimeError("pipeline exploded")

    worker = _make_worker(staging, failing_callback)
    status, _errors = worker._process_matches(
        _candidate(staging, str(audio)), _identification(),
        _match_result(str(audio)))

    assert status == "failed"
    # routing survives for the retry — the next attempt still lands own
    assert sidecar.exists()
