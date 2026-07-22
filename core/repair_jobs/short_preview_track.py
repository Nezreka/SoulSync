"""Repair job: detect ~30s PREVIEW clips and re-fetch the full track.

The HiFi endpoint (and occasionally others) sometimes deliver a ~30-second preview/sample
instead of the full song. Those land in the library looking like real tracks. This job
finds short tracks, looks up the EXPECTED length from the track's metadata source, and —
when the source says the real track is meaningfully longer than the file — flags it as a
preview clip.

Approving the finding (in repair_worker._fix_short_preview_track) deletes the preview file,
drops the DB row so the track goes missing again, and re-adds it to the wishlist with a full
payload so the real version gets downloaded. The scan itself ONLY creates findings — nothing
is deleted, removed, or wishlisted without the user approving, exactly like the other tools.

Conservative by design (it deletes a file): a track is only flagged when the source confirms
it should be much longer. Genuine short tracks (intros, skits, interludes — where the source
agrees the track is short) are left alone, and tracks whose length can't be verified from a
source are skipped, never flagged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.short_preview")


def _art_from_details(details: Dict[str, Any]) -> Optional[str]:
    """Pull a renderable album-art URL out of a get_track_details() response. The cleaned dict
    doesn't carry images, but raw_data does: Spotify → raw_data.album.images[0].url, iTunes →
    raw_data.artworkUrl100 (upscaled). Returns None if neither is present."""
    raw = (details or {}).get("raw_data") or {}
    album = raw.get("album")
    if isinstance(album, dict):
        images = album.get("images")
        if isinstance(images, list) and images and isinstance(images[0], dict) and images[0].get("url"):
            return images[0]["url"]
    art = raw.get("artworkUrl100") or raw.get("artworkUrl60") or raw.get("artworkUrl30")
    if art:
        # iTunes serves tiny thumbnails by default; bump to a usable size.
        for small in ("100x100bb", "60x60bb", "30x30bb"):
            art = art.replace(small, "600x600bb")
        return art
    return None


@register_job
class ShortPreviewTrackJob(RepairJob):
    job_id = "short_preview_track"
    display_name = "Preview Clip Cleanup"
    description = (
        "Finds ~30s preview clips that slipped in instead of the full song (common from the "
        "HiFi endpoint) and re-fetches the real track."
    )
    help_text = (
        "Some downloads — especially via the HiFi source — deliver a ~30-second preview clip "
        "instead of the full song. They look like normal tracks in your library. This job scans "
        "for short tracks, checks how long the track ACTUALLY is from its metadata source "
        "(Spotify / iTunes / MusicBrainz), and flags any whose real length is much greater than "
        "the file — i.e. a preview.\n\n"
        "Approving a finding deletes the preview file, removes the track from the database (so it "
        "shows as missing), and re-adds it to your Wishlist so the full version downloads.\n\n"
        "It's conservative: genuine short tracks (intros, skits) where the source agrees the track "
        "is short are left alone, and tracks whose length can't be verified are skipped.\n\n"
        "Settings:\n"
        "  - max_duration_seconds: only tracks at or below this length are considered (default 30).\n"
        "  - min_expected_drift_seconds: the source must say the real track is at least this many "
        "seconds longer than the file before it's flagged (default 30)."
    )
    icon = "scissors"
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {
        "max_duration_seconds": 30,
        "min_expected_drift_seconds": 30,
        "verify_zero_length": True,
    }
    setting_options: Dict[str, list] = {}
    auto_fix = False

    def _setting_bool(self, context: JobContext, key: str, default: bool) -> bool:
        cm = getattr(context, "config_manager", None)
        if cm is None:
            return default
        val = cm.get(self.get_config_key(key), default)
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on")
        return bool(val)

    def _decoded_seconds(self, file_path: str, context: JobContext) -> float:
        """Real decoded length of a library file via ffmpeg. Resolves the
        stored (media-server-view) path to one this process can read first —
        without the base dirs, Docker/NAS installs resolve every path to None
        (#1000). 0.0 when the file can't be found or decoded."""
        import os

        from core.imports.file_integrity import probe_decoded_duration
        from core.library.path_resolver import resolve_library_file_path
        if not file_path:
            return 0.0
        resolved = resolve_library_file_path(
            file_path,
            transfer_folder=getattr(context, "transfer_folder", None),
            config_manager=getattr(context, "config_manager", None),
        )
        if not resolved and os.path.isfile(file_path):
            resolved = file_path
        return probe_decoded_duration(resolved) if resolved else 0.0

    def _setting_int(self, context: JobContext, key: str, default: int) -> int:
        cm = getattr(context, "config_manager", None)
        if cm is None:
            return default
        try:
            return int(cm.get(self.get_config_key(key), default) or default)
        except (TypeError, ValueError):
            return default

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        max_dur_s = self._setting_int(context, "max_duration_seconds", 30)
        min_drift_s = self._setting_int(context, "min_expected_drift_seconds", 30)
        max_dur_ms = max_dur_s * 1000
        # HiFi's HLS-assembled FLAC previews store duration 0 (total_samples=0),
        # so the stored-duration filter alone MISSES them — the exact clips that
        # replaced sella's tracks. When on (default), also pull zero/NULL-duration
        # owned files and DECODE them with ffmpeg to get the real length.
        verify_zero = self._setting_bool(context, "verify_zero_length", True)

        conn = context.db._get_connection()
        try:
            cursor = conn.cursor()
            where_dur = ("(t.duration IS NULL OR t.duration = 0 OR t.duration <= ?)"
                         if verify_zero else
                         "t.duration IS NOT NULL AND t.duration > 0 AND t.duration <= ?")
            cursor.execute(
                f"""
                SELECT t.id, t.title, t.duration, t.file_path,
                       t.spotify_track_id, t.itunes_track_id, t.musicbrainz_recording_id,
                       ar.name AS artist_name, ar.thumb_url AS artist_thumb,
                       al.title AS album_title, al.thumb_url AS album_thumb,
                       ar.id AS artist_row_id
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE {where_dur}
                  AND t.file_path IS NOT NULL AND t.file_path != ''
                """,
                (max_dur_ms,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        total = len(rows)
        if context.report_progress:
            try:
                context.report_progress(phase=f"Checking {total} short tracks for previews…", total=total)
            except Exception:  # noqa: S110 — progress is best-effort
                pass

        for i, row in enumerate(rows):
            if context.check_stop() or context.wait_if_paused():
                break
            result.scanned += 1

            title = row["title"] or "Unknown"
            artist = row["artist_name"] or "Unknown"
            # Live progress EVERY track — the source lookup below is a network call, so without
            # per-track reporting the UI looks frozen at "Starting…" (the #937-follow-up report).
            if context.update_progress:
                try:
                    context.update_progress(i + 1, total)
                except Exception:  # noqa: S110 — best-effort
                    pass
            if context.report_progress:
                try:
                    context.report_progress(
                        phase=f"Checking {i + 1}/{total} short tracks for previews…",
                        log_line=f"{artist} — {title}", scanned=i + 1, total=total,
                    )
                except Exception:  # noqa: S110 — best-effort
                    pass

            file_dur_s = (row["duration"] or 0) / 1000.0
            # Zero/unknown stored duration = the HiFi fragmented-FLAC shape.
            # Decode the real length so a faked-full/zero-header preview is
            # measured by its actual audio, not its lying header.
            if file_dur_s <= 0 and verify_zero:
                real_s = self._decoded_seconds(row["file_path"], context)
                if real_s <= 0:
                    result.skipped += 1        # can't measure → never flag
                    continue
                file_dur_s = real_s
            elif file_dur_s <= 0:
                result.skipped += 1
                continue

            source = self._lookup_source(context, row)

            # Can't verify the real length → never flag (a delete must be backed by evidence).
            if source is None:
                result.skipped += 1
                continue
            expected_dur_s = source["duration_s"]
            # Prefer the source's album art (a renderable CDN url) over the library thumb, which
            # is often empty/non-renderable for un-enriched HiFi previews → art-less wishlist orb.
            album_image = source.get("album_image") or row["album_thumb"]

            # Source agrees the track is short (genuine intro/skit) → leave it alone. Only a
            # source that says the real track is MUCH longer than the file marks a preview.
            if (expected_dur_s - file_dur_s) < min_drift_s:
                result.skipped += 1
                continue

            if context.create_finding:
                title = row["title"] or "Unknown"
                artist = row["artist_name"] or "Unknown"
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type="short_preview_track",
                        severity="warning",
                        entity_type="track",
                        entity_id=str(row["id"]),
                        file_path=row["file_path"],
                        title=f"Preview clip: {artist} - {title}",
                        description=(
                            f'File is {file_dur_s:.0f}s but "{title}" by {artist} is '
                            f"{expected_dur_s:.0f}s at the source — looks like a preview clip. "
                            "Approve to delete it and re-download the full version."
                        ),
                        details={
                            "track_id": row["id"],
                            "title": row["title"],
                            "artist": row["artist_name"],
                            "album": row["album_title"],
                            "album_thumb_url": album_image,
                            "artist_thumb_url": row["artist_thumb"],
                            "artist_id": row["artist_row_id"],
                            "file_duration_s": round(file_dur_s, 1),
                            "expected_duration_s": round(expected_dur_s, 1),
                            "original_path": row["file_path"],
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as exc:
                    logger.debug("create_finding failed for track %s: %s", row["id"], exc)
                    result.errors += 1

        return result

    def _lookup_source(self, context: JobContext, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Look the track up at its metadata source: returns {'duration_s', 'album_image'} or
        None when no source id is usable / the lookup fails. The SAME lookup that confirms the
        real length also carries the album art (in raw_data), which we capture so the re-wishlist
        isn't art-less when the library album thumb is missing (the #937-follow-up: HiFi previews
        on un-enriched albums). Every metadata client exposes get_track_details(id)."""

        def _build(details) -> Optional[Dict[str, Any]]:
            ms = (details or {}).get("duration_ms")
            if not ms or ms <= 0:
                return None
            return {"duration_s": ms / 1000.0, "album_image": _art_from_details(details)}

        # Spotify — pass allow_fallback=False. The default fallback scrapes the configured
        # metadata source, which is slow and can BLOCK a scan loop indefinitely when the
        # official API isn't authed (the #937-follow-up hang). Official-only is fast and
        # returns None cleanly when unavailable, so we just move to the next source.
        sp_id = row.get("spotify_track_id")
        if sp_id and context.spotify_client and not context.is_spotify_rate_limited():
            try:
                r = _build(context.spotify_client.get_track_details(str(sp_id), allow_fallback=False))
                if r:
                    return r
            except TypeError:
                pass  # older client without the flag — skip, don't risk the slow path
            except Exception as exc:
                logger.debug("spotify lookup failed for %s: %s", sp_id, exc)

        # iTunes (public API, no auth, fast) then MusicBrainz.
        for source_id, client in (
            (row.get("itunes_track_id"), context.itunes_client),
            (row.get("musicbrainz_recording_id"), context.mb_client),
        ):
            if not source_id or client is None:
                continue
            getter = getattr(client, "get_track_details", None)
            if getter is None:
                continue
            try:
                r = _build(getter(str(source_id)))
                if r:
                    return r
            except Exception as exc:
                logger.debug("lookup failed for %s: %s", source_id, exc)
        return None

    def estimate_scope(self, context: JobContext) -> int:
        try:
            max_dur_ms = self._setting_int(context, "max_duration_seconds", 30) * 1000
            verify_zero = self._setting_bool(context, "verify_zero_length", True)
            where_dur = ("(duration IS NULL OR duration = 0 OR duration <= ?)"
                         if verify_zero else "duration > 0 AND duration <= ?")
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT COUNT(*) FROM tracks WHERE {where_dur} "
                    "AND file_path IS NOT NULL AND file_path != ''",
                    (max_dur_ms,),
                )
                return (cursor.fetchone() or [0])[0]
            finally:
                conn.close()
        except Exception:
            return 0
