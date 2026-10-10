"""Manual subtitle search/download + per-title language overrides ("Replace Bazarr"
Phase 3 backend).

Endpoints (exact contract — the webui builder depends on it):

- GET    /api/video/subtitles/overrides/<kind>/<item_id>  (kind: movie|show)
         → {"languages": [...], "source": "override"|"global", "global_languages": [...]}
- PUT    /api/video/subtitles/overrides/<kind>/<item_id>  {"languages": ["en","es"]}
         → {"ok": true, "languages": [...]}  (400 on bad kind / bad codes)
- DELETE /api/video/subtitles/overrides/<kind>/<item_id> → {"ok": true}
- GET    /api/video/subtitles/search/<kind>/<item_id>?lang=en  (kind: movie|episode)
         → {"candidates": [{candidate_id, provider, title, score, hash_match,
                            download_count, language, hi, forced}]}  (score desc)
- POST   /api/video/subtitles/manual-download
         {"kind", "item_id", "lang", "candidate_id"}
         → {"ok": true, "path": "..."}  (404 unknown/expired candidate, 400 bad input)

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


def register_routes(bp):
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
        query = SubtitleQuery(identity=dict(identity), language=lang,
                              filename=filename, moviehash=moviehash)
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
        return jsonify({"candidates": out})

    @bp.route("/subtitles/manual-download", methods=["POST"])
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
        return jsonify({"ok": True, "path": path})
