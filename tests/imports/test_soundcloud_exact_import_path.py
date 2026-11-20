"""an exact soundcloud upload (core/wishlist/soundcloud_exact) never lands on
a catalogue file's path.

the upload is tagged with the original's names, so the default single
template ($albumartist/$albumartist - $title/$title) points it at the owned
original's own file. the import pipeline's existing-file branch would then
drop the download as redundant (or quality-replace the original) and count a
success, and the import's match would be filed against the original. the
destination carries the soundcloud id instead, and an existing file at that
path fails the import.

real import pipeline, real path builder and atomic move; only metadata
services and audio probes are faked, so the bytes on disk prove which file
went where.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.imports import paths, pipeline
from core.imports.file_integrity import IntegrityResult
from core.quality.model import AudioQuality
from core.wishlist import soundcloud_exact as exact
from database.music_database import MusicDatabase
from tests import lib2_seed

TEMPLATES = {
    "album_path": "$albumartist/$albumartist - $album/$track - $title",
    "single_path": "$albumartist/$albumartist - $title/$title",
}


def _payload():
    """an exact payload as build_payload makes it, the user tagging the flip
    with the original's names."""
    from core.soundcloud_client import SoundcloudClient
    client = SoundcloudClient(download_path="/nonexistent-sc")
    result = client._sc_to_track_result({
        "id": "123", "title": "Artist A - Song", "uploader": "djuser",
        "webpage_url": "https://soundcloud.com/djuser/song-flip",
        "url": "https://soundcloud.com/djuser/song-flip", "duration": 201.0})
    return exact.build_payload(result, "https://soundcloud.com/djuser/song-flip",
                               title="Song", artists=["Artist A"])


@pytest.fixture
def exact_import(tmp_path, monkeypatch):
    library = tmp_path / "library"
    original = library / "Artist A" / "Artist A - Song" / "Song.mp3"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original audio")
    incoming = tmp_path / "downloads" / "Artist A - Song.mp3"
    incoming.parent.mkdir(parents=True)
    incoming.write_bytes(b"upload audio")
    profile = {"ranked_targets": [{"format": "mp3", "min_bitrate": 128}],
               "replace_lower_quality": True, "deep_audio_verify": False,
               "lossy_copy_enabled": False, "downsample_hires": False}
    settings = {
        "soulseek.transfer_path": str(library),
        "soulseek.download_path": str(tmp_path / "downloads"),
        "post_processing.replaygain_enabled": False,
        "file_organization.templates": TEMPLATES,
    }
    cfg = SimpleNamespace(get=lambda key, default=None: settings.get(key, default),
                          get_active_media_server=lambda: None)
    payload = _payload()
    context = {
        "track_info": {**payload, "quality_profile_id": 17},
        "original_search_result": {"title": "Song", "artist": "Artist A"},
        "_quality_profile": profile,
        "is_album_download": False,
    }
    settled = []
    monkeypatch.setattr(pipeline, "config_manager", cfg)
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pipeline, "get_import_context_artist", lambda _: {"name": "Artist A"})
    monkeypatch.setattr(pipeline, "get_import_has_clean_metadata", lambda _: True)
    monkeypatch.setattr(pipeline, "build_import_album_info", lambda *a, **k: {
        "is_album": False, "album_name": "Song", "track_number": 1,
        "disc_number": 1, "clean_track_name": "Song", "source": "soundcloud_exact",
    })
    monkeypatch.setattr(pipeline, "resolve_album_group", lambda a, b, c: "Song")
    monkeypatch.setattr(pipeline, "get_import_clean_title", lambda *a, **k: "Song")
    monkeypatch.setattr(pipeline, "get_audio_quality_string", lambda *a, **k: "")
    monkeypatch.setattr(pipeline, "check_quality_target", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "check_audio_integrity",
                        lambda *a, **k: IntegrityResult(ok=True, checks={}))
    monkeypatch.setattr(pipeline, "_audio_length_seconds", lambda _: 200.0)
    monkeypatch.setattr("mutagen.File", lambda _: SimpleNamespace(
        tags={"title": "Song", "artist": "Artist A", "album": "Song"}))
    # the upload ranks above the original: the old path quality-replaced it
    monkeypatch.setattr("core.imports.quality_replace.probe_audio_quality",
                        lambda path: AudioQuality("mp3", 320 if Path(path).read_bytes() == b"upload audio"
                                                  else 128))
    for name in ("enhance_file_metadata", "download_cover_art", "generate_lrc_file",
                 "cleanup_empty_directories", "cleanup_slskd_dedup_siblings",
                 "emit_track_downloaded", "record_library_history_download",
                 "record_download_provenance"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: None)
    # the library writer registers the imported file at its final path
    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda: db)
    monkeypatch.setattr("core.imports.side_effects.get_database", lambda: db)
    monkeypatch.setattr(pipeline, "get_database", lambda: db)
    registered = {}

    def register(ctx, *_a, **_k):
        final = ctx.get("_final_processed_path")
        with db._get_connection() as conn:
            registered[final] = lib2_seed.track(conn, "Artist A", "Song", "Song", path=final)
            conn.commit()
    monkeypatch.setattr(pipeline, "record_soulsync_library_entry", register)
    monkeypatch.setattr(pipeline, "check_and_remove_from_wishlist",
                        lambda ctx, published_path=None, **_k: settled.append(published_path))
    monkeypatch.setattr(pipeline, "matched_downloads_context", {})
    monkeypatch.setattr(pipeline, "post_process_locks", {})
    monkeypatch.setattr(pipeline, "processed_download_ids", set())
    monkeypatch.setattr("core.library2.track_reconcile_trigger.schedule_file_track_reconcile",
                        lambda *a, **kw: None)

    def run():
        pipeline.post_process_matched_download("exact-1", context, str(incoming), SimpleNamespace())

    return SimpleNamespace(original=original, incoming=incoming, context=context, run=run, db=db,
                           settled=settled, registered=registered,
                           upload=original.with_name("Song [123].mp3"))


def test_the_template_points_an_exact_upload_at_its_own_path(exact_import):
    c = exact_import
    plain = dict(c.context, track_info={**c.context["track_info"], "id": "sp1"})
    album = {"is_album": False, "album_name": "Song", "clean_track_name": "Song", "track_number": 1}
    catalogue_path, _ = paths.build_final_path_for_track(plain, {"name": "Artist A"}, album, ".mp3")
    assert catalogue_path == str(c.original)   # the collision the gate found
    exact_path, _ = paths.build_final_path_for_track(c.context, {"name": "Artist A"}, album, ".mp3")
    assert exact_path == str(c.upload)
    # idempotent: a path that already carries the id keeps one
    assert paths.exact_upload_path(c.context, exact_path) == exact_path


def test_exact_import_never_touches_the_owned_original(exact_import):
    c = exact_import
    c.run()
    assert c.context.get("_pipeline_import_succeeded"), c.context.get("_context_failure_msg")
    assert c.original.read_bytes() == b"original audio"
    assert c.upload.read_bytes() == b"upload audio"
    assert not c.incoming.exists()
    assert c.context["_final_processed_path"] == str(c.upload)
    assert c.settled == [str(c.upload)]


def test_the_recorded_match_points_at_the_upload(exact_import):
    """the published path the import hands the wishlist is what the exact
    match is filed against (resolution._record_exact_match)."""
    from core.wishlist.resolution import _record_exact_match

    c = exact_import
    with c.db._get_connection() as conn:   # the owned original
        lib2_seed.track(conn, "Artist A", "Song", "Song", path=str(c.original))
        conn.commit()
    c.run()
    assert list(c.registered) == [str(c.upload)]
    _record_exact_match("soundcloud_exact_123", c.context["track_info"], c.settled[0], c.db, [1])
    match = c.db.get_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123")
    assert match["library_file_path"] == str(c.upload)
    assert str(match["library_track_id"]) == str(c.registered[str(c.upload)])
    assert exact.upload_file(c.db, {"id": "soundcloud_exact_123"}, 1) == str(c.upload)


def test_an_existing_file_at_the_exact_path_fails_the_import(exact_import):
    """the backstop: whatever is already at the upload's own path is never
    proof the upload is in, nor a file to replace or a reason to drop it."""
    c = exact_import
    c.upload.write_bytes(b"something else")
    c.run()
    assert not c.context.get("_pipeline_import_succeeded")
    assert pipeline.import_rejection_reason(c.context)
    assert not pipeline.kept_existing_library_file(c.context)
    assert c.upload.read_bytes() == b"something else"
    assert c.original.read_bytes() == b"original audio"
    assert c.incoming.read_bytes() == b"upload audio"
    assert c.settled == []


def test_a_catalogue_import_still_lands_on_the_template_path(exact_import):
    """the existing-file branch is unchanged for everything else: the better
    catalogue download replaces the original (replace_lower_quality on)."""
    c = exact_import
    c.context["track_info"] = {**c.context["track_info"], "id": "sp1"}
    c.context["track_info"].pop(exact.EXACT_BLOCK)
    c.run()
    assert c.original.read_bytes() == b"upload audio"
    assert not c.upload.exists()
