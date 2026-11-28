"""Manual subtitle search/download + per-title language overrides ("Replace Bazarr"
Phase 3 backend).

Endpoints (exact contract — the webui builder depends on it):

- GET    /api/video/subtitles/overrides/<kind>/<item_id>  (kind: movie|show)
         → {"languages": [...], "source": "override"|"global", "global_languages": [...]}
- PUT    /api/video/subtitles/overrides/<kind>/<item_id>  {"languages": ["en","es"]}
         → {"ok": true, "languages": [...]}  (400 on bad kind / bad codes)
- DELETE /api/video/subtitles/overrides/<kind>/<item_id> → {"ok": true}
- GET    /api/video/subtitles/search/<kind>/<item_id>?lang=en[&forced=1]  (kind: movie|episode)
         → {"candidates": [{candidate_id, provider, title, score, hash_match,
                            download_count, language, hi, forced}]}  (score desc)
- POST   /api/video/subtitles/manual-download
         {"kind", "item_id", "lang", "candidate_id"}
         → {"ok": true, "path": "..."}  (404 unknown/expired candidate, 400 bad input)

AI subtitles (whisper on this server, its own queue):

- GET    /api/video/subtitles/ai/jobs?kind=&item_id=&active=1
         → {"jobs": [{id, video_kind, video_id, language, state, progress,
                      eta_seconds, position, confidence, verdict, ...}],
            "available": bool, "why": str|null, "watching": bool}
- POST   /api/video/subtitles/ai/jobs {"kind", "item_id", "lang", "origin": player|detail|library}
         → 202 {"job": {...}}  (503 + plain-words "message" when ai can't run here)
- POST   /api/video/subtitles/ai/jobs/<id>/cancel → {"ok": true}
- GET    /api/video/subtitles/ai/hardware?refresh=1 → {summary, cuda, gpu, cores, ..., recommended}
- POST   /api/video/subtitles/ai/bench {"model"} → 202 (admin; the quick test, result in whisper/status)
- POST   /api/video/subtitles/whisper/download/cancel → {"ok": true} (admin)
- DELETE /api/video/subtitles/whisper/models/<name> → {"ok": true} (admin)

The search endpoint caches its candidate list server-side (10-minute TTL,
keyed by a uuid candidate_id per candidate) so the download endpoint resolves
the user's pick WITHOUT re-searching.

Reads only video.db; isolated from the music API. Auth: the blueprint's
before_request gate in api/video/__init__.py handles it — override writes are
admin-only (per-title library management, like the detail title overrides);
manual-download requires can_download (it's a download action); the GETs stay
open.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

from core.connect.device_access import device_playback
from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.subtitles")

#: How long a manual-search candidate list stays resolvable for download.
SEARCH_TTL_S = 600
#: Cap per search so one huge provider response can't bloat the cache.
MAX_CANDIDATES = 50

_search_cache: dict = {}          # candidate_id -> entry
_search_lock = threading.Lock()


def _cache_prune_locked(now: float) -> None:
    for cid in [c for c, e in _search_cache.items() if e["expires"] <= now]:
        del _search_cache[cid]


def _cache_store(entry: dict) -> str:
    """Store one candidate entry; returns its candidate_id."""
    cid = uuid.uuid4().hex
    now = time.monotonic()
    with _search_lock:
        _cache_prune_locked(now)
        _search_cache[cid] = dict(entry, expires=now + SEARCH_TTL_S)
    return cid


def _cache_get(candidate_id: str):
    """The entry for a candidate_id, or None when unknown/expired."""
    now = time.monotonic()
    with _search_lock:
        _cache_prune_locked(now)
        entry = _search_cache.get(candidate_id)
        return dict(entry) if entry else None


def _clear_search_cache_for_tests():
    with _search_lock:
        _search_cache.clear()


def _whisper_store_download_result(db, model, info, error):
    """Persist a finished model download into the organization blob
    (load-modify-save). Runs on the download worker thread; never raises.

    Success marks the model downloaded with its path/size/timestamp. Failure
    recomputes the flag from disk truth — a failed re-download must not wipe
    a still-good previous download, and must not claim a model whose file is
    gone.
    """
    from core.video import organization
    from core.video.subtitles.providers import whisper as whisper_mod
    try:
        current = organization.load(db)
    except Exception:  # noqa: BLE001
        current = {}
    try:
        if info is not None:
            current.update({
                "whisper_model": info["name"],
                "whisper_model_downloaded": True,
                "whisper_model_path": info["path"],
                "whisper_model_size_bytes": info["size_bytes"],
                "whisper_model_downloaded_at": info["downloaded_at"],
            })
        else:
            # Failure: recompute the flag from disk truth. If the requested
            # model isn't on disk but another one is (e.g. the user switched
            # the picker then the download failed), restore whisper_model to
            # the working one instead of orphaning a good setup.
            on_disk = [m for m in whisper_mod.WHISPER_MODELS
                       if whisper_mod.model_file_exists(m)]
            if whisper_mod.model_file_exists(model):
                current["whisper_model_downloaded"] = True
            elif on_disk:
                current["whisper_model"] = on_disk[0]
                current["whisper_model_downloaded"] = True
            else:
                current["whisper_model_downloaded"] = False
        organization.save(db, current)
    except Exception:  # noqa: BLE001 - persistence is bookkeeping, never fatal
        logger.exception("whisper: could not persist download state")


def _get_setting(db, settings, key, default=None):
    """Blob-first, top-level fallback — the same get_setting the import hook
    and worker build: tuning knobs live in the organization blob, provider
    secrets top-level."""
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    top_get = getattr(db, "get_setting", None)
    if callable(top_get):
        return top_get(key, default)
    return default


def _settings_for(db):
    try:
        from core.video import organization
        return organization.load(db)
    except Exception:  # noqa: BLE001
        return {}


def _sync_in_background(path, video_path, text, provider_id, get_setting) -> None:
    from core.video.subtitles.worker import _maybe_autosync
    try:
        synced = _maybe_autosync(text, video_path, provider_id, get_setting)
        if synced and synced != text and os.path.exists(path):
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(synced)
            os.replace(tmp, path)
            logger.info("auto-sync fixed the timing of %s", os.path.basename(path))
    except Exception:  # noqa: BLE001 - sync is a bonus
        logger.debug("background auto-sync failed for %s", path, exc_info=True)


def tracks_in_file(file_path, lang, forced, get_setting) -> list:
    """The subtitle tracks this video already has in ``lang``: sidecars beside
    it and tracks inside it. manual search shows them, so a title whose french
    subs only live inside the file says so instead of 'none found' (#2126).
    ``bitmap`` marks a picture track (PGS, VobSub): players show it, but it
    can't be turned into a .srt without OCR. Never raises."""
    from core.video.subtitles import existing as existing_subs
    from core.video.subtitles.languages import satisfies_language
    if not file_path or not os.path.exists(file_path):
        return []
    try:
        tracks = existing_subs.existing_tracks(file_path, get_setting)
    except Exception:  # noqa: BLE001 - a bonus line, never a failed search
        logger.debug("in-file subtitle check failed for %s", file_path, exc_info=True)
        return []
    bitmap = existing_subs._PGS_CODECS | existing_subs._VOBSUB_CODECS
    return [{"language": t.language, "where": t.source, "forced": bool(t.forced),
             "hi": bool(t.hi), "codec": t.codec, "bitmap": t.codec in bitmap}
            for t in tracks
            if satisfies_language(t.language, lang) and bool(t.forced) == bool(forced)]


_bench_lock = threading.Lock()
_bench_state: dict = {"running": None, "error": None}


def _bench_results() -> dict:
    """{model: {speed, seconds, device, at}} from the top-level setting."""
    import json
    try:
        from . import get_video_db
        raw = get_video_db().get_setting("whisper_bench")
        data = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _bench_public() -> dict:
    with _bench_lock:
        return {"running": _bench_state.get("running"),
                "error": "the quick test failed, see the server log" if _bench_state.get("error") else None}


def _run_bench(db, name, settings) -> None:
    """the quick test on its own thread; the result goes to the
    ``whisper_bench`` setting (outside the settings page's form, so a save
    there never wipes it)."""
    import json
    from core.video.subtitles.ai import hardware
    from core.video.subtitles.providers import whisper as whisper_mod
    try:
        info = hardware.detect()
        pref = str(settings.get("whisper_device") or "auto")
        device = "cuda" if pref in ("auto", "cuda") and info.get("cuda") else "cpu"
        threads = int(settings.get("ai_subs_threads") or 0) or max(1, (os.cpu_count() or 2) - 1)
        res = hardware.run_bench(str(whisper_mod.model_dir(name)), device=device,
                                 compute_type="int8_float16" if device == "cuda" else "int8",
                                 threads=0 if device == "cuda" else threads)
        data = _bench_results()
        data[name] = res
        db.set_setting("whisper_bench", json.dumps(data))
        with _bench_lock:
            _bench_state.update(running=None, error=None)
    except Exception as e:  # noqa: BLE001
        logger.exception("whisper quick test failed for %s", name)
        with _bench_lock:
            _bench_state.update(running=None, error=str(e))


def register_routes(bp):
    @bp.route("/subtitles/whisper/status", methods=["GET"])
    def subtitles_whisper_status():
        from . import get_video_db
        from core.video.subtitles.providers import whisper as whisper_mod
        db = get_video_db()
        settings = _settings_for(db)

        def get_setting(k, d=None):
            return _get_setting(db, settings, k, d)

        try:
            payload = whisper_mod.status_payload(get_setting)
        except Exception:  # noqa: BLE001 - status is best-effort
            logger.exception("whisper status failed")
            payload = {"error": "status unavailable"}
        return jsonify(payload)

    @bp.route("/subtitles/whisper/download", methods=["POST"])
    def subtitles_whisper_download():
        from . import get_video_db
        from core.video.subtitles.providers import whisper as whisper_mod
        body = request.get_json(silent=True) or {}
        model = body.get("model")
        if whisper_mod.valid_model_name(model) is None:
            return jsonify({"error": "unknown model — choose one of: %s"
                            % ", ".join(sorted(whisper_mod.WHISPER_MODELS))}), 400
        db = get_video_db()

        def _on_complete(info, error):
            _whisper_store_download_result(db, whisper_mod.valid_model_name(model),
                                           info, error)

        ok, reason = whisper_mod.start_download(model, on_complete=_on_complete)
        if not ok:
            code = 409 if "already in progress" in str(reason) else 400
            return jsonify({"error": reason}), code
        return jsonify({"ok": True,
                        "model": whisper_mod.valid_model_name(model)}), 202

    @bp.route("/subtitles/whisper/download/cancel", methods=["POST"])
    def subtitles_whisper_download_cancel():
        from core.video.subtitles.providers import whisper as whisper_mod
        return jsonify({"ok": True, "cancelled": whisper_mod.cancel_download()})

    @bp.route("/subtitles/whisper/models/<name>", methods=["DELETE"])
    def subtitles_whisper_delete_model(name):
        from core.video.subtitles.providers import whisper as whisper_mod
        if whisper_mod.valid_model_name(name) is None:
            return jsonify({"error": "unknown model"}), 400
        try:
            removed = whisper_mod.delete_model(name)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 409
        except OSError:
            logger.exception("whisper: delete failed for %s", name)
            return jsonify({"error": "couldn't remove the model files"}), 500
        return jsonify({"ok": True, "removed": removed})

    @bp.route("/subtitles/ai/hardware", methods=["GET"])
    def subtitles_ai_hardware():
        from core.video.subtitles.ai import hardware
        info = hardware.detect(refresh=str(request.args.get("refresh") or "") in ("1", "true"))
        return jsonify(dict(info, summary=hardware.summary(info),
                            recommended=hardware.recommend(info, _bench_results())))

    @bp.route("/subtitles/ai/bench", methods=["POST"])
    def subtitles_ai_bench():
        from . import get_video_db
        from core.video.subtitles.providers import whisper as whisper_mod
        body = request.get_json(silent=True) or {}
        name = whisper_mod.valid_model_name(body.get("model"))
        if name is None or not whisper_mod.model_file_exists(name):
            return jsonify({"error": "install that model first"}), 400
        if whisper_mod.transcription_engine() is None:
            return jsonify({"error": "the faster-whisper package is missing on the server"}), 503
        with _bench_lock:
            if _bench_state.get("running"):
                return jsonify({"error": "a quick test is already running"}), 409
            _bench_state.update(running=name, error=None)
        db = get_video_db()
        settings = _settings_for(db)
        threading.Thread(target=_run_bench, args=(db, name, settings), daemon=True,
                         name="whisper-bench").start()
        return jsonify({"ok": True, "model": name}), 202

    @bp.route("/subtitles/ai/jobs", methods=["GET"])
    @device_playback
    def subtitles_ai_jobs():
        from . import get_video_db
        from core.video.subtitles.ai import queue as aiq
        db = get_video_db()
        kind = request.args.get("kind")
        item_id = request.args.get("item_id", type=int)
        active = str(request.args.get("active") or "") in ("1", "true")
        rows = db.ai_job_list(active_only=active, limit=100,
                              video_kind=kind if kind else None,
                              video_id=item_id if kind and item_id is not None else None)
        jobs = [aiq.public_job(r, db.ai_job_position(r["id"])
                               if r["state"] in ("queued", "running", "paused") else None)
                for r in rows]
        settings = _settings_for(db)
        return jsonify({"jobs": jobs, "available": aiq.availability(settings) is None,
                        "why": aiq.availability(settings), "watching": aiq.someone_watching(),
                        "auto": bool(settings.get("whisper_enabled")), "bench": _bench_public()})

    @bp.route("/subtitles/ai/jobs", methods=["POST"])
    @device_playback
    def subtitles_ai_enqueue():
        from . import get_video_db
        from core.profile_context import get_current_profile_id
        from core.video.subtitles.ai import queue as aiq
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("item_id")
        if kind not in ("movie", "episode") or not isinstance(item_id, int):
            return jsonify({"error": "kind must be movie|episode and item_id an int"}), 400
        origin = str(body.get("origin") or "detail")
        if origin not in ("player", "detail", "library"):
            origin = "detail"
        db = get_video_db()
        if db.subtitle_source_exists(kind, item_id) is False:
            return jsonify({"error": "not found"}), 404
        try:
            job = aiq.enqueue(db, kind, item_id, body.get("lang") or "en", origin=origin,
                              profile_id=get_current_profile_id())
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except aiq.NotAvailable as e:
            return jsonify({"error": "unavailable", "message": str(e)}), 503
        return jsonify({"job": aiq.public_job(job, db.ai_job_position(job["id"]))}), 202

    @bp.route("/subtitles/ai/jobs/<int:job_id>/cancel", methods=["POST"])
    @device_playback
    def subtitles_ai_cancel(job_id):
        from . import get_video_db
        from core.profile_context import get_current_profile_id, is_admin_request
        from core.video.subtitles.ai import queue as aiq
        db = get_video_db()
        job = db.ai_job_get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        try:
            admin = bool(is_admin_request())
        except Exception:  # noqa: BLE001
            admin = False
        if not admin and job.get("created_by") not in (None, get_current_profile_id()):
            return jsonify({"error": "only the profile that asked (or an admin) can cancel this"}), 403
        ok = db.ai_job_cancel(job_id)
        aiq.wake()
        return jsonify({"ok": ok})

    @bp.route("/subtitles/overrides/<kind>/<int:item_id>", methods=["GET"])
    def subtitles_get_override(kind, item_id):
        from . import get_video_db
        from core.video.subtitles import (
            effective_subtitle_languages, parse_langs, SubtitleLookupError,
        )
        if kind not in ("movie", "show"):
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        if db.subtitle_source_exists(kind, item_id) is False:
            return jsonify({"error": "not found"}), 404
        settings = _settings_for(db)
        global_langs = parse_langs(settings.get("subtitle_langs"))
        try:
            override = db.subtitle_override_get(kind, item_id)
        except Exception:  # noqa: BLE001 - transient: display degrades to global
            override = None
        if kind == "movie":
            # A transient lookup failure reads as UNKNOWN — serve the global
            # list rather than 500ing a read-only display endpoint.
            try:
                langs = effective_subtitle_languages(db, "movie", item_id, settings)
            except SubtitleLookupError:
                langs = list(global_langs)
        else:
            langs = list(override) if override is not None else list(global_langs)
        return jsonify({"languages": langs,
                        "source": "override" if override is not None else "global",
                        "global_languages": global_langs})

    @bp.route("/subtitles/overrides/<kind>/<int:item_id>", methods=["PUT"])
    def subtitles_put_override(kind, item_id):
        from . import get_video_db
        from core.video.subtitles import validate_lang_codes
        if kind not in ("movie", "show"):
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        if db.subtitle_source_exists(kind, item_id) is False:
            return jsonify({"error": "not found"}), 404
        body = request.get_json(silent=True) or {}
        try:
            langs = validate_lang_codes(body.get("languages"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        stored = db.subtitle_override_set(kind, item_id, langs)
        return jsonify({"ok": True, "languages": stored})

    @bp.route("/subtitles/overrides/<kind>/<int:item_id>", methods=["DELETE"])
    def subtitles_delete_override(kind, item_id):
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        if db.subtitle_source_exists(kind, item_id) is False:
            return jsonify({"error": "not found"}), 404
        db.subtitle_override_clear(kind, item_id)
        return jsonify({"ok": True})

    @bp.route("/subtitles/search/<kind>/<int:item_id>", methods=["GET"])
    @device_playback
    def subtitles_search(kind, item_id):
        from . import get_video_db
        from core.video.subtitles import providers as providers_pkg
        from core.video.subtitles import scoring as scoring_mod
        from core.video.subtitles import validate_lang_codes
        from core.video.subtitles.providers.base import SubtitleQuery
        from core.video.subtitles.worker import _resolve_file
        if kind not in ("movie", "episode"):
            return jsonify({"error": "kind must be movie|episode"}), 400
        try:
            lang = validate_lang_codes([request.args.get("lang") or "en"])[0]
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        db = get_video_db()
        if db.subtitle_source_exists(kind, item_id) is False:
            return jsonify({"error": "not found"}), 404
        settings = _settings_for(db)

        def get_setting(k, d=None):
            return _get_setting(db, settings, k, d)

        file_path, identity = _resolve_file(
            db, {"video_kind": kind, "video_id": item_id})
        filename, moviehash = None, None
        if file_path and os.path.exists(file_path):
            filename = os.path.basename(file_path)
            try:
                moviehash = scoring_mod.opensubtitles_hash(file_path)
            except Exception:  # noqa: BLE001 - hash is a bonus, never fatal
                moviehash = None
        # Without a resolvable file the query runs blind (no filename/hash):
        # scores carry little signal and the download endpoint will refuse
        # (it can't name the sidecar) — the search still shows what's out
        # there rather than failing outright.
        try:
            from core.video.subtitles.worker import _hi_forced_pref
            _pref = _hi_forced_pref(get_setting)
        except Exception:  # noqa: BLE001
            _pref = "off"
        # forced=1 searches forced (foreign-parts-only) subs; full and forced
        # never mix. manual=True keeps hi and plain side by side — the user
        # is the one choosing.
        forced = str(request.args.get("forced") or "").strip().lower() in ("1", "true", "yes")
        query = SubtitleQuery(identity=dict(identity), language=lang, forced=forced,
                              filename=filename, moviehash=moviehash,
                              file_path=file_path, hi_forced_pref=_pref, manual=True)
        from core.video.subtitles import parse_provider_order
        order = parse_provider_order(settings.get("subtitle_provider_order"))
        try:
            ranked = providers_pkg.rank_candidates(query, order, get_setting)
        except Exception:  # noqa: BLE001 - search is best-effort
            logger.exception("subtitle search failed for %s %s", kind, item_id)
            ranked = []
        out = []
        for score, pid, _provider, cand in ranked[:MAX_CANDIDATES]:
            entry = {
                "kind": kind, "item_id": int(item_id), "lang": lang,
                "file_path": file_path,
                "candidate": {
                    "provider_id": pid,
                    "download_ref": cand.download_ref,
                    "language": lang,
                    "hi": bool(getattr(cand, "hi", False)),
                    "forced": bool(getattr(cand, "forced", False)),
                    "title": str(getattr(cand, "title", "") or ""),
                    "score": round(float(score), 1),
                    "download_count": int(getattr(cand, "download_count", 0) or 0),
                    "hash_match": bool(getattr(cand, "hash_match", False)),
                },
            }
            cid = _cache_store(entry)
            c = entry["candidate"]
            out.append({"candidate_id": cid, "provider": c["provider_id"],
                        "title": c["title"], "score": c["score"],
                        "hash_match": c["hash_match"],
                        "download_count": c["download_count"],
                        "language": c["language"], "hi": c["hi"],
                        "forced": c["forced"]})
        return jsonify({"candidates": out,
                        "in_file": tracks_in_file(file_path, lang, forced, get_setting)})

    @bp.route("/subtitles/manual-download", methods=["POST"])
    @device_playback
    def subtitles_manual_download():
        from . import get_video_db
        from core.video.subtitles import providers as providers_pkg
        from core.video.subtitles import srt_name, validate_lang_codes
        from core.video.subtitles.providers.base import SubtitleCandidate
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("item_id")
        if kind not in ("movie", "episode") or not isinstance(item_id, int):
            return jsonify({"error": "kind must be movie|episode and "
                                      "item_id an int"}), 400
        try:
            lang = validate_lang_codes([body.get("lang")])[0]
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        candidate_id = body.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            return jsonify({"error": "candidate_id is required"}), 400
        entry = _cache_get(candidate_id)
        if entry is None:
            return jsonify({"error": "unknown or expired candidate_id — "
                                      "search again"}), 404
        if (entry.get("kind") != kind or entry.get("item_id") != item_id
                or entry.get("lang") != lang):
            return jsonify({"error": "candidate does not match this "
                                      "video/language"}), 400
        file_path = entry.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return jsonify({"error": "video file not available — cannot "
                                      "place the sidecar"}), 400
        db = get_video_db()
        settings = _settings_for(db)

        def get_setting(k, d=None):
            return _get_setting(db, settings, k, d)

        c = entry["candidate"]
        provider = providers_pkg.get_providers().get(c["provider_id"])
        try:
            configured = provider is not None and provider.is_configured(get_setting)
        except Exception:  # noqa: BLE001 - a broken config check reads as unconfigured
            configured = False
        if not configured:
            return jsonify({"error": "provider '%s' is not configured"
                            % c["provider_id"]}), 400
        candidate = SubtitleCandidate(
            provider_id=c["provider_id"], language=c["language"],
            hi=c["hi"], forced=c["forced"], title=c["title"],
            download_ref=c["download_ref"],
            hash_match=c["hash_match"], download_count=c["download_count"])
        try:
            text = provider.download(candidate)
        except Exception:  # noqa: BLE001
            logger.exception("manual subtitle download failed")
            text = None
        if not text:
            return jsonify({"error": "provider download failed"}), 502
        name = srt_name(file_path, lang, hi=c["hi"], forced=c["forced"])
        path = os.path.join(os.path.dirname(file_path), name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            logger.exception("manual subtitle sidecar write failed for %s", path)
            return jsonify({"error": "could not write sidecar: %s" % e}), 500
        # Explicit user pick: the wanted row is created/kept with user_set=1
        # so the Phase 3 upgrade loop never replaces the user's choice.
        # NOTE: this endpoint does NOT burn the automated per-provider daily
        # quota — it's an explicit user action, not the worker's background
        # fetching, and throttling a deliberate click would be wrong.
        db.subtitle_want(kind, item_id, lang, hi=c["hi"], forced=c["forced"],
                         user_set=True)
        db.subtitle_set_user_set(kind, item_id, lang, True,
                                 hi=c["hi"], forced=c["forced"])
        db.subtitle_mark(kind, item_id, lang, "have",
                         hi=c["hi"], forced=c["forced"])
        db.subtitle_log_fetch(kind, item_id, lang, "downloaded",
                              provider=str(c["provider_id"]),
                              candidate_title=c["title"], score=c["score"],
                              hi=c["hi"], forced=c["forced"])
        # a human pick replaces an ai subtitle for the same language
        from core.video.subtitles.worker import _retire_ai
        _retire_ai(db, file_path, lang, c["provider_id"], c["forced"])
        # timing: a manual pick gets the same auto-sync as the worker's
        # downloads, on a thread (ffsubsync takes 20 to 30 s). the player's
        # cache is keyed by mtime, so the fixed file shows on its next load
        if bool(get_setting("subtitle_autosync", False)):
            threading.Thread(target=_sync_in_background,
                             args=(path, file_path, text, c["provider_id"], get_setting),
                             daemon=True, name="subtitle-autosync").start()
        return jsonify({"ok": True, "path": path})
