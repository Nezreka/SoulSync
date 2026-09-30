"""Sample Studio API (Phase 1) — analysis + waveform peaks.

Phase 3 — chop rendering + stash: POST /sample/preview (fast librosa
render for auditioning pitch/BPM changes), POST /sample/chop (final render
+ stash row), stash CRUD, ZIP export.

Registered on the /api/v1 blueprint via register_routes (see api/__init__.py),
same @require_api_key auth as the other v1 modules.

Phase 2 addition: the web UI talks to the legacy session-auth /api/* routes
(webui/src/app/api-client.ts has no API key), so the pure service functions
below are shared with thin legacy wrappers in web_server.py
(/api/sample/analysis, /api/sample/analyze, /api/sample/peaks, …).
"""

import io
import json
import os
import re
import uuid
import zipfile

from flask import request, send_file

from database.music_database import get_database
from utils.logging_config import get_logger
from .auth import require_api_key
from .helpers import api_success, api_error

logger = get_logger("api.sample")


class SampleHttpError(Exception):
    """Service-layer error carrying an HTTP status for either route family."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _track_exists(track_id: int) -> bool:
    db = get_database()
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _require_track(track_id: int) -> None:
    if not _track_exists(track_id):
        raise SampleHttpError("NOT_FOUND", f"unknown track_id {track_id}", 404)


def _resolve_track_path(track_id: int) -> str:
    """DB file_path -> a file that exists on disk. Shared by peaks/preview/chop."""
    return _resolve_source_path(track_id, stem=None)


def _resolve_source_path(track_id: int, stem: str | None) -> str:
    """Track audio, or one separated stem when `stem` is given.

    Raises SampleHttpError 409 when a stem is requested but the track was
    never separated (or separation failed).
    """
    from core.sample import store as sample_store

    if stem:
        from core.sample import stems as stems_mod

        if stem not in stems_mod.ALL_STEMS:
            raise SampleHttpError("BAD_REQUEST", f"unknown stem {stem!r}", 400)
        path = sample_store.stem_file_path(track_id, stem)
        if not path or not os.path.isfile(path):
            raise SampleHttpError(
                "STEMS_MISSING",
                "split the track first, from the Stems panel under the editor",
                409,
            )
        return path

    stored = sample_store.get_track_file_path(track_id)
    if not stored:
        raise SampleHttpError("NOT_FOUND", f"no file path for track {track_id}", 404)
    # One shared choke point (core.sample.worker): raw path first, then the
    # playback resolver web_server injects, so a track that plays also chops.
    from core.sample import worker as sample_worker

    path = sample_worker.resolve_track_file(stored)
    if not path:
        raise SampleHttpError("FILE_MISSING", sample_worker.unreachable_message(stored), 409)
    return path


def _merge_track_rows(passes, limit):
    """Merge pass results in order, deduped, capped at `limit`."""
    from api.serializers import serialize_track

    seen = set()
    tracks = []
    for rows in passes:
        for row in rows:
            if len(tracks) >= limit:
                break
            row_id = row.get("id")
            if row_id in seen:
                continue
            seen.add(row_id)
            tracks.append(serialize_track(row))
        if len(tracks) >= limit:
            break
    return tracks


def search_library_tracks(q=None, title="", artist="", limit=50):
    """Track search backing the Sample Studio library panel.

    One normalized free-text query matched against title, artist name and
    per-track artist credit in a single statement
    (MusicDatabase.search_tracks_interactive): exact title hits first, then
    title prefixes, then artist hits. The old artist-then-title double
    cascade (up to six full-table scans per keystroke on a big library) is
    what made this search feel stuck. An explicit title/artist pair (no q)
    keeps the matcher's dual-constraint semantics. Returns serialized
    tracks, capped at `limit`. Errors propagate so the web wrapper can
    return a 500 and the UI can tell "search failed" from "no matches".
    """
    q = (q or "").strip()
    title = (title or "").strip()
    artist = (artist or "").strip()
    if not q and not (title or artist):
        raise SampleHttpError("BAD_REQUEST", "q, title, or artist is required", 400)
    limit = max(1, min(int(limit or 50), 200))

    db = get_database()
    if q:
        rows = db.search_tracks_interactive(q, limit=limit)
    else:
        # explicit title/artist pair keeps the matcher's dual-constraint
        # semantics (title must match AND artist must match)
        rows = db.api_search_tracks(title=title, artist=artist, limit=limit)
    return _merge_track_rows([rows], limit)


def fetch_analysis(track_id: int, retry: bool = False):
    """Cached analysis row, or enqueue + return pending status.

    Returns (payload_dict, http_status). Never raises except SampleHttpError.
    Worker-recorded errors are sticky (see core.sample.worker) so a polling
    client actually observes the failure; pass retry=True to clear a recorded
    error and queue the track again.
    """
    _require_track(track_id)
    try:
        from core.sample import store as sample_store
        from core.sample import worker as sample_worker

        row = sample_store.get_analysis(track_id)
        if row is not None and sample_store.is_current(track_id, row):
            return _analysis_payload(row, "done"), 200
        # never analyzed, made by an older analyzer, or the file changed since.
        # queue it, and hand back whatever we had so the page isn't blank
        status = sample_worker.enqueue_analysis(track_id, retry=retry)
        if status == "done":  # finished between the two checks
            row = sample_store.get_analysis(track_id)
            return _analysis_payload(row, "done"), 200
        payload = _analysis_payload(row, status) if row else {"track_id": track_id, "status": status}
        return payload, 202
    except SampleHttpError:
        raise
    except Exception as e:
        logger.error("sample/analysis failed for track %s: %s", track_id, e)
        raise SampleHttpError("ANALYSIS_ERROR", str(e), 500) from e


def _analysis_payload(row: dict, status: str) -> dict:
    out = {k: v for k, v in row.items() if k != "source_sig"}
    out["status"] = status
    return out


def enqueue_track_analysis(track_id: int):
    """Enqueue background analysis for a track. Idempotent.

    A sticky worker error is surfaced as a 422 failure envelope rather than
    a 200 "success" — the caller needs to know the track did NOT queue.
    """
    _require_track(track_id)
    try:
        from core.sample import worker as sample_worker

        status = sample_worker.enqueue_analysis(track_id)
        if status.startswith("error:"):
            return {"track_id": track_id, "status": status}, 422
        return {"track_id": track_id, "status": status}, 200
    except SampleHttpError:
        raise
    except Exception as e:
        logger.error("sample/analyze failed for track %s: %s", track_id, e)
        raise SampleHttpError("ANALYSIS_ERROR", str(e), 500) from e


def fetch_peaks(track_id: int, buckets: int, stem: str | None = None):
    """Min/max waveform peaks, computed once per (track, buckets, stem) then cached."""
    _require_track(track_id)
    try:
        from core.sample import stems as stems_mod
        from core.sample import store as sample_store
        from core.sample.analyze import compute_peaks

        if stem is not None and stem not in stems_mod.ALL_STEMS:
            raise SampleHttpError("BAD_REQUEST", f"unknown stem {stem!r}", 400)
        if not 16 <= buckets <= 20000:
            raise SampleHttpError("BAD_REQUEST", f"buckets must be 16..20000, got {buckets}", 400)
        path = _resolve_source_path(track_id, stem)
        # the file's signature is in the cache name, so a replaced file gets
        # a fresh waveform instead of the old one
        cache_file = sample_store.peaks_path(track_id, buckets, stem,
                                             sig=sample_store.source_signature(path))
        if os.path.isfile(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f), 200
        peaks = compute_peaks(path, buckets=buckets)
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(peaks, f)
        os.replace(tmp, cache_file)
        sample_store.drop_stale_peaks(track_id, buckets, stem, keep=cache_file)
        return peaks, 200
    except SampleHttpError:
        raise
    except ValueError as e:
        raise SampleHttpError("BAD_REQUEST", str(e), 400) from e
    except Exception as e:
        logger.error("sample/peaks failed for track %s: %s", track_id, e)
        raise SampleHttpError("PEAKS_ERROR", str(e), 500) from e


def _handle_service_error(e: SampleHttpError):
    return api_error(e.code, e.message, e.status)


# ── Phase 3: chop rendering + stash ────────────────────────────────────

_PREVIEW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.wav$")
_CHOP_FORMATS = ("wav16", "wav24", "flac")


def _parse_render_params(data: dict):
    """Shared validation for preview/chop bodies. Returns a clean dict."""
    try:
        track_id = int(data.get("track_id") or 0)
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "track_id is required", 400) from None
    try:
        start_s = float(data.get("start_s", 0))
        end_s = float(data.get("end_s", 0))
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "start_s and end_s must be numbers", 400) from None
    try:
        pitch_st = float(data.get("pitch_st") or 0)
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "pitch_st must be a number", 400) from None
    target_bpm = data.get("target_bpm")
    if target_bpm is not None:
        try:
            target_bpm = float(target_bpm)
        except (TypeError, ValueError):
            raise SampleHttpError("BAD_REQUEST", "target_bpm must be a number", 400) from None
        if not 20 <= target_bpm <= 300:
            raise SampleHttpError("BAD_REQUEST", "target_bpm must be 20..300", 400)
    if not abs(pitch_st) <= 24:
        raise SampleHttpError("BAD_REQUEST", "pitch_st must be -24..24", 400)
    if not end_s > start_s >= 0:
        raise SampleHttpError("BAD_REQUEST", "need 0 <= start_s < end_s", 400)
    _require_track(track_id)
    stem = data.get("stem")
    if stem is not None:
        from core.sample import stems as stems_mod

        if stem not in stems_mod.ALL_STEMS:
            raise SampleHttpError("BAD_REQUEST", f"unknown stem {stem!r}", 400)
    from core.sample.fx import parse_fx

    try:
        fx = parse_fx(data)
    except ValueError as e:
        raise SampleHttpError("BAD_REQUEST", str(e), 400) from None
    return {
        "track_id": track_id,
        "start_s": start_s,
        "end_s": end_s,
        "pitch_st": pitch_st,
        "target_bpm": target_bpm,
        "stem": stem,
        "fx": fx,
    }


def _source_bpm_for_stretch(track_id: int, target_bpm) -> float | None:
    """BPM to stretch from, or None when no stretch was requested.

    Raises 409 when a target BPM is given but the track was never analyzed.
    """
    if not target_bpm:
        return None
    from core.sample import store as sample_store

    row = sample_store.get_analysis(track_id)
    if not row or not row.get("bpm"):
        raise SampleHttpError(
            "BPM_UNKNOWN",
            "target_bpm needs the track's BPM — open it in Studio once so analysis runs",
            409,
        )
    return float(row["bpm"])


def _delay_bpm(track_id: int, target_bpm, fx) -> float | None:
    """tempo the delay locks to: the stretched tempo, else the track's own.

    409 when the delay is on and neither is known.
    """
    if fx is None or not fx.needs_bpm:
        return None
    if target_bpm:
        return float(target_bpm)
    from core.sample import store as sample_store

    row = sample_store.get_analysis(track_id)
    if not row or not row.get("bpm"):
        raise SampleHttpError(
            "BPM_UNKNOWN",
            "delay follows the beat, so it needs the track's tempo. give the analysis a moment",
            409,
        )
    return float(row["bpm"])


def render_preview(track_id: int, start_s: float, end_s: float, pitch_st: float,
                   target_bpm, stem: str | None = None, fx=None) -> tuple:
    """Fast librosa render of the in/out region for auditioning.

    Returns ({"preview_id", "engine", "duration_s"}, 200). The file is served
    by GET /sample/preview/<preview_id>; stale files are purged opportunistically.
    When `stem` is given the region is rendered from that separated stem.
    """
    from core.sample import render as sample_render
    from core.sample import store as sample_store

    path = _resolve_source_path(track_id, stem)
    source_bpm = _source_bpm_for_stretch(track_id, target_bpm)
    delay_bpm = _delay_bpm(track_id, target_bpm, fx)
    sample_store.cleanup_previews()
    preview_id = f"p{track_id}_{uuid.uuid4().hex}.wav"
    out_path = os.path.join(sample_store.previews_dir(), preview_id)
    try:
        result = sample_render.render_chop(
            path, start_s, end_s,
            pitch_st=pitch_st, target_bpm=target_bpm, source_bpm=source_bpm,
            engine="preview", out_path=out_path, out_format="wav16", preview=True,
            fx=fx, delay_bpm=delay_bpm,
        )
    except ValueError as e:
        raise SampleHttpError("BAD_REQUEST", str(e), 400) from e
    return {
        "preview_id": preview_id,
        "engine": result["engine"],
        "duration_s": result["duration_s"],
    }, 200


def preview_file_path(preview_id: str) -> str:
    """Filesystem path for a preview id, or raise 404 on anything sketchy."""
    from core.sample import store as sample_store

    if not _PREVIEW_NAME_RE.match(preview_id or ""):
        raise SampleHttpError("NOT_FOUND", "unknown preview", 404)
    path = os.path.join(sample_store.previews_dir(), preview_id)
    if not os.path.isfile(path):
        raise SampleHttpError("NOT_FOUND", "preview expired — render it again", 404)
    return path


def save_chop(track_id: int, start_s: float, end_s: float, pitch_st: float,
              target_bpm, name: str, tags: list, format: str,
              stem: str | None = None, folder: str | None = None, fx=None) -> tuple:
    """Final render (Rubber Band when available) + stash row (file + bookmark).

    When `stem` is given the chop is cut from that separated stem; the stem is
    recorded on the entry so the bookmark knows its true source.

    `folder` picks the destination sample folder (one of the configured
    ``library.sample_paths``, default the first). The chop is rendered to a
    temp file, tagged, then atomically moved to
    ``<folder>/<sample_path template>`` — the user's folder never sees a
    partial render. Unknown folders refuse with 400.
    """
    import tempfile

    from core.sample import folders as sample_folders
    from core.sample import organize as sample_organize
    from core.sample import render as sample_render
    from core.sample import store as sample_store
    from core.sample import tags as sample_tags

    name = (name or "").strip()
    if not name:
        raise SampleHttpError("BAD_REQUEST", "name is required", 400)
    if len(name) > 120:
        raise SampleHttpError("BAD_REQUEST", "name is too long (120 chars max)", 400)
    if format not in _CHOP_FORMATS:
        raise SampleHttpError("BAD_REQUEST", f"format must be one of {_CHOP_FORMATS}", 400)
    tags = [str(t)[:40] for t in (tags or [])][:20]

    try:
        dest_folder = sample_folders.resolve_sample_folder(folder)
    except ValueError as e:
        raise SampleHttpError("BAD_REQUEST", str(e), 400) from e

    path = _resolve_source_path(track_id, stem)
    source_bpm = _source_bpm_for_stretch(track_id, target_bpm)
    delay_bpm = _delay_bpm(track_id, target_bpm, fx)
    ext = ".flac" if format == "flac" else ".wav"

    meta = sample_store.get_track_metadata(track_id)
    segments = sample_organize.render_sample_path(
        sample_organize.sample_template(),
        {
            "artist": meta["artist"],
            "track": meta["title"],
            "album": meta["album"],
            "chop": name,
            "stem": stem or "",
        },
    )

    fd, tmp_path = tempfile.mkstemp(prefix="chop_", suffix=ext, dir=dest_folder)
    os.close(fd)
    try:
        result = sample_render.render_chop(
            path, start_s, end_s,
            pitch_st=pitch_st, target_bpm=target_bpm, source_bpm=source_bpm,
            engine="auto", out_path=tmp_path, out_format=format, preview=False,
            fx=fx, delay_bpm=delay_bpm,
        )
    except ValueError as e:
        _unlink_quiet(tmp_path)
        raise SampleHttpError("BAD_REQUEST", str(e), 400) from e
    except Exception:
        _unlink_quiet(tmp_path)
        raise

    # Tag the render (best-effort — a chop without tags is still a chop).
    try:
        # Stem chops render from a generated WAV with no embedded art — look
        # the cover up from the original library track instead.
        art_source = path
        if stem:
            try:
                art_source = _resolve_source_path(track_id, None) or path
            except SampleHttpError:
                pass
        comment = sample_tags.build_comment(
            meta["title"] or name, meta["artist"], meta["album"],
            pitch_st=pitch_st, target_bpm=target_bpm, source_bpm=source_bpm,
            stem=stem,
        )
        sample_tags.tag_chop_file(
            tmp_path, format,
            {
                "title": name,
                "artist": meta["artist"],
                "album": sample_tags.CHOP_ALBUM,
                "comment": comment,
            },
            art=sample_tags.extract_cover_art(art_source),
        )
    except Exception as e:
        logger.warning("tagging chop %s failed (non-fatal): %s", tmp_path, e)

    out_path = sample_organize.unique_chop_path(dest_folder, segments, ext)
    try:
        os.replace(tmp_path, out_path)
    except Exception:
        _unlink_quiet(tmp_path)
        raise

    try:
        entry = sample_store.create_stash_entry(
            name=name, tags=tags, track_id=track_id,
            start_s=start_s, end_s=end_s, pitch_st=pitch_st,
            target_bpm=target_bpm, format=format, file_path=out_path,
            stem=stem, folder=dest_folder, fx=fx,
        )
    except Exception:
        _unlink_quiet(out_path)
        raise
    entry["engine"] = result["engine"]
    entry["duration_s"] = result["duration_s"]
    return entry, 201


def _unlink_quiet(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def trim_selection(track_id: int, start_s: float, end_s: float,
                   stem: str | None = None) -> tuple:
    """Tighten [start_s, end_s) to the part that actually sounds.

    Returns ({"start_s", "end_s", "trimmed"}, 200). an all-silent selection
    comes back unchanged with trimmed=False.
    """
    from core.sample import render as sample_render
    from core.sample.trim import sounding_bounds

    if not end_s > start_s >= 0:
        raise SampleHttpError("BAD_REQUEST", "need 0 <= start_s < end_s", 400)
    if end_s - start_s > sample_render.MAX_CHOP_SECONDS:
        raise SampleHttpError("BAD_REQUEST", "selection too long to trim", 400)
    path = _resolve_source_path(track_id, stem)
    y, sr, _ = sample_render.decode_region(path, start_s, end_s)
    bounds = sounding_bounds(y, sr)
    if bounds is None:
        return {"start_s": start_s, "end_s": end_s, "trimmed": False}, 200
    new_start = round(start_s + bounds[0], 4)
    new_end = round(min(end_s, start_s + bounds[1]), 4)
    trimmed = new_start > start_s + 1e-4 or new_end < end_s - 1e-4
    return {"start_s": new_start, "end_s": new_end, "trimmed": trimmed}, 200


def parse_trim_body(data: dict) -> dict:
    try:
        track_id = int(data.get("track_id") or 0)
        start_s = float(data.get("start_s", 0))
        end_s = float(data.get("end_s", 0))
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "track_id, start_s and end_s are required", 400) from None
    _require_track(track_id)
    stem = data.get("stem") or None
    if stem is not None:
        from core.sample import stems as stems_mod

        if stem not in stems_mod.ALL_STEMS:
            raise SampleHttpError("BAD_REQUEST", f"unknown stem {stem!r}", 400)
    return {"track_id": track_id, "start_s": start_s, "end_s": end_s, "stem": stem}


def list_sample_folders() -> tuple:
    """Configured sample output folders + which is the default."""
    from core.sample import folders as sample_folders

    folders = sample_folders.get_sample_folders()
    return {"folders": folders, "default": folders[0] if folders else None}, 200


def list_stash_entries() -> tuple:
    from core.sample import store as sample_store

    return {"entries": sample_store.list_stash()}, 200


def remove_stash_entry(entry_id: int) -> tuple:
    from core.sample import store as sample_store

    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "entry id must be an integer", 400) from None
    if not sample_store.delete_stash_entry(entry_id):
        raise SampleHttpError("NOT_FOUND", f"unknown stash entry {entry_id}", 404)
    return {"deleted": entry_id}, 200


def stash_audio_path(entry_id: int) -> tuple:
    """(file_path, mimetype) for a stash entry's rendered audio."""
    from core.sample import store as sample_store

    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        raise SampleHttpError("BAD_REQUEST", "entry id must be an integer", 400) from None
    entry = sample_store.get_stash_entry(entry_id)
    if not entry or not entry["file_path"] or not os.path.isfile(entry["file_path"]):
        raise SampleHttpError("NOT_FOUND", "chop audio is missing", 404)
    mimetype = "audio/flac" if entry["format"] == "flac" else "audio/wav"
    return entry["file_path"], mimetype


def export_stash_zip() -> tuple:
    """(BytesIO, filename) — a zip of every stash WAV/FLAC."""
    from core.sample import store as sample_store

    entries = sample_store.list_stash()
    if not entries:
        raise SampleHttpError("EMPTY", "nothing in the stash to export", 400)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            if not e["file_path"] or not os.path.isfile(e["file_path"]):
                continue
            safe = re.sub(r"[^A-Za-z0-9 _.-]", "", e["name"]).strip() or "chop"
            ext = ".flac" if e["format"] == "flac" else ".wav"
            zf.write(e["file_path"], f"{e['id']:04d}_{safe}{ext}")
    buf.seek(0)
    return buf, "sample-stash.zip"


# ── Phase 4: stem separation ─────────────────────────────────────────

def _stems_payload(track_id: int, status: str, method: str) -> dict:
    from core.sample import stems as stems_mod
    from core.sample import store as sample_store

    payload: dict = {
        "track_id": track_id,
        "status": status,
        "method": method,
        "stems": [],
        "labels": {},
        "stems_available": stems_mod.stems_available(),
        "rough_available": True,
    }
    if status == "done":
        info = sample_store.get_stems(track_id, method)
        if info:
            payload["stems"] = list(info["stems"].keys())
            payload["labels"] = {s: stems_mod.STEM_LABELS[s] for s in payload["stems"]}
            payload["backend"] = info["backend"]
        else:
            # rows vanished (files deleted, source replaced) — say so, allow retry
            payload["status"] = "idle"
    return payload


def separate_stems(track_id: int, backend: str | None = None, method: str | None = None) -> tuple:
    """Enqueue a split for a track. Idempotent.

    method: demucs (needs torch) | rough-drums | rough-center (built in).
    backend: 'stub' forces the test separator for demucs, nothing else.
    Returns 202 while queued/running, 200 when done. poll GET /sample/stems/status.
    """
    _require_track(track_id)
    from core.sample import stems as stems_mod

    method = method or "demucs"
    if method not in stems_mod.SEPARATION_METHODS:
        raise SampleHttpError(
            "BAD_REQUEST",
            f"method must be one of {', '.join(stems_mod.SEPARATION_METHODS)}",
            400,
        )
    if method == "demucs" and backend != "stub" and not stems_mod.stems_available():
        # refuse honestly instead of downloading a model the server can't run
        raise SampleHttpError(
            "STEMS_UNAVAILABLE",
            "stem separation needs torch, torchaudio and demucs installed on the server",
            409,
        )
    try:
        from core.sample import stems_worker as sw

        status = sw.enqueue_separation(track_id, backend=backend, method=method)
        payload = _stems_payload(track_id, status, method)
        return payload, 202 if payload["status"] in ("queued", "running") else 200
    except SampleHttpError:
        raise
    except Exception as e:
        logger.error("sample/stems failed for track %s: %s", track_id, e)
        raise SampleHttpError("STEMS_ERROR", str(e), 500) from e


def stems_status(track_id: int, method: str | None = None) -> tuple:
    """Current split status (the last method asked for, unless one is named)."""
    _require_track(track_id)
    try:
        from core.sample import stems as stems_mod
        from core.sample import stems_worker as sw

        if method is not None and method not in stems_mod.SEPARATION_METHODS:
            raise SampleHttpError("BAD_REQUEST", f"unknown method {method!r}", 400)
        method = method or sw.current_method(track_id)
        status = sw.get_status(track_id, method)
        return _stems_payload(track_id, status, method), 200
    except SampleHttpError:
        raise
    except Exception as e:
        logger.error("sample/stems/status failed for track %s: %s", track_id, e)
        raise SampleHttpError("STEMS_ERROR", str(e), 500) from e


def stem_audio_path(track_id: int, stem: str) -> tuple:
    """(file_path, mimetype) for one separated stem."""
    from core.sample import stems as stems_mod

    _require_track(track_id)
    if stem not in stems_mod.ALL_STEMS:
        raise SampleHttpError("BAD_REQUEST", f"unknown stem {stem!r}", 400)
    try:
        from core.sample import store as sample_store

        path = sample_store.stem_file_path(track_id, stem)
    except Exception as e:
        raise SampleHttpError("STEMS_ERROR", str(e), 500) from e
    if not path or not os.path.isfile(path):
        raise SampleHttpError(
            "STEMS_MISSING",
            "this track hasn't been split that way yet",
            404,
        )
    return path, "audio/wav"


def register_routes(bp):

    # ── Analysis ─────────────────────────────────────────────────────

    @bp.route("/sample/analysis", methods=["GET"])
    @require_api_key
    def sample_analysis_get():
        """Cached analysis for a track, or 202 + enqueue when not ready.

        This is the lazy backfill path: opening a never-analyzed track in
        Studio enqueues it and the client polls until status == done.
        Worker-recorded errors are sticky so the poll observes them;
        pass ?retry=1 to clear a recorded error and queue again.
        """
        try:
            track_id = int(request.args.get("track_id") or 0)
        except (TypeError, ValueError):
            return api_error("BAD_REQUEST", "track_id is required", 400)
        try:
            retry = (request.args.get("retry") or "") == "1"
            payload, status = fetch_analysis(track_id, retry=retry)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/analyze", methods=["POST"])
    @require_api_key
    def sample_analyze_post():
        """Enqueue background analysis for a track. Idempotent."""
        data = request.get_json(silent=True) or {}
        try:
            track_id = int(data.get("track_id") or 0)
        except (TypeError, ValueError):
            return api_error("BAD_REQUEST", "track_id is required", 400)
        try:
            payload, status = enqueue_track_analysis(track_id)
            if status != 200:
                # Sticky worker error: the track did NOT queue. Report the
                # failure honestly instead of a 200 "success".
                return api_error("ANALYSIS_ERROR", payload["status"], status)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    # ── Waveform peaks ───────────────────────────────────────────────

    @bp.route("/sample/peaks", methods=["GET"])
    @require_api_key
    def sample_peaks_get():
        """Min/max waveform peaks for the editor canvas.

        Computed once per (track, buckets), cached as JSON under
        <db_dir>/sample-studio/peaks/, served from cache after.
        """
        try:
            track_id = int(request.args.get("track_id") or 0)
            buckets = int(request.args.get("buckets") or 1500)
        except (TypeError, ValueError):
            return api_error("BAD_REQUEST", "track_id and buckets must be integers", 400)
        try:
            payload, status = fetch_peaks(track_id, buckets, stem=request.args.get("stem") or None)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    # ── Phase 3: chop rendering + stash ──────────────────────────────

    @bp.route("/sample/preview", methods=["POST"])
    @require_api_key
    def sample_preview_post():
        """Fast render of the in/out region for auditioning pitch/BPM changes.

        Body: {track_id, start_s, end_s, pitch_st?, target_bpm?, stem?,
        normalize?, fade_ms?, reverse?, space?, delay?}.
        Returns {preview_id, engine, duration_s}; the audio is served by
        GET /sample/preview/<preview_id>. Slices over 60s are refused (400).
        """
        data = request.get_json(silent=True) or {}
        try:
            params = _parse_render_params(data)
            payload, status = render_preview(**params)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/preview/<preview_id>", methods=["GET"])
    @require_api_key
    def sample_preview_get(preview_id):
        """Serve a rendered preview file (short-lived cache)."""
        try:
            path = preview_file_path(preview_id)
            return send_file(path, mimetype="audio/wav", conditional=True)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/chop", methods=["POST"])
    @require_api_key
    def sample_chop_post():
        """Final render + stash row (file + bookmark).

        Body: {track_id, start_s, end_s, pitch_st?, target_bpm?, stem?, name, tags?,
        format?, folder?, normalize?, fade_ms?, reverse?, space?, delay?}.
        format: wav16 | wav24 | flac (default wav16).
        folder: destination sample folder — one of the configured
        library.sample_paths (default: the first). Anything else is a 400.
        """
        data = request.get_json(silent=True) or {}
        try:
            params = _parse_render_params(data)
            payload, status = save_chop(
                **params,
                name=data.get("name"),
                tags=data.get("tags"),
                format=data.get("format") or "wav16",
                folder=data.get("folder"),
            )
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/trim", methods=["POST"])
    @require_api_key
    def sample_trim_post():
        """Tighten a selection to its sounding audio.

        Body: {track_id, start_s, end_s, stem?}. Returns {start_s, end_s, trimmed}.
        """
        data = request.get_json(silent=True) or {}
        try:
            payload, status = trim_selection(**parse_trim_body(data))
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/folders", methods=["GET"])
    @require_api_key
    def sample_folders_get():
        """Configured sample output folders + the default destination."""
        try:
            payload, status = list_sample_folders()
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stash", methods=["GET"])
    @require_api_key
    def sample_stash_get():
        """All stash entries, newest first."""
        try:
            payload, status = list_stash_entries()
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stash/<int:entry_id>", methods=["DELETE"])
    @require_api_key
    def sample_stash_delete(entry_id):
        """Delete a stash entry — row and rendered file."""
        try:
            payload, status = remove_stash_entry(entry_id)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stash/<int:entry_id>/audio", methods=["GET"])
    @require_api_key
    def sample_stash_audio(entry_id):
        """Serve a stash entry's rendered audio."""
        try:
            path, mimetype = stash_audio_path(entry_id)
            return send_file(path, mimetype=mimetype, conditional=True)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stash/export", methods=["GET"])
    @require_api_key
    def sample_stash_export():
        """ZIP of every stash WAV/FLAC."""
        try:
            buf, filename = export_stash_zip()
            return send_file(buf, mimetype="application/zip", as_attachment=True,
                             download_name=filename)
        except SampleHttpError as e:
            return _handle_service_error(e)

    # ── Phase 4: stem separation ─────────────────────────────────────

    @bp.route("/sample/stems", methods=["POST"])
    @require_api_key
    def sample_stems_post():
        """Enqueue stem separation for a track. Idempotent.

        Body: {track_id, method?, backend?}. method: demucs (default) |
        rough-drums | rough-center. backend 'stub' forces the test separator.
        Returns 202 while queued/running, 200 when already done.
        """
        data = request.get_json(silent=True) or {}
        try:
            track_id = int(data.get("track_id") or 0)
        except (TypeError, ValueError):
            return api_error("BAD_REQUEST", "track_id is required", 400)
        backend = data.get("backend")
        try:
            payload, status = separate_stems(track_id, backend=backend, method=data.get("method"))
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stems/status", methods=["GET"])
    @require_api_key
    def sample_stems_status():
        """Poll separation progress: queued|running|done|error|idle."""
        try:
            track_id = int(request.args.get("track_id") or 0)
        except (TypeError, ValueError):
            return api_error("BAD_REQUEST", "track_id is required", 400)
        try:
            payload, status = stems_status(track_id, method=request.args.get("method") or None)
            return api_success(payload, status=status)
        except SampleHttpError as e:
            return _handle_service_error(e)

    @bp.route("/sample/stems/<int:track_id>/<stem>/audio", methods=["GET"])
    @require_api_key
    def sample_stem_audio(track_id, stem):
        """Serve one split output (a demucs stem or a rough output) as WAV."""
        try:
            path, mimetype = stem_audio_path(track_id, stem)
            return send_file(path, mimetype=mimetype, conditional=True)
        except SampleHttpError as e:
            return _handle_service_error(e)
